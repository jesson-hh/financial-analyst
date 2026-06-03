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

// 短窗口判定 (<30 交易日 KPI 年化放大失真) + 区间总收益替代年化
function _isShort(d) {
  return !!(d && d.nav && d.nav.dates && d.nav.dates.length < 30);
}
function _intervalRet(series) {
  if (!series || series.length < 2) return null;
  const a = series[0], b = series[series.length - 1];
  if (typeof a !== 'number' || typeof b !== 'number' || a === 0) return null;
  return (b - a) / a;
}

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

function Kpi({ label, value, hint, dir, last, tooltip }) {
  return (
    <div title={tooltip} style={{ padding: '12px 14px', borderRight: last ? 'none' : '1px solid var(--line-soft)', background: 'rgba(255,255,255,0.4)' }}>
      <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>
        {label}{tooltip && <span style={{ fontSize: 8, color: 'var(--ink-3)', marginLeft: 2 }}>ⓘ</span>}
      </div>
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
          {!factors.length && (
            <div style={{ padding: 24 }}>
              <Empty label="暂无因子" />
              <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', marginTop: 12, lineHeight: 1.6 }}>
                {family === 'user' ? <>切到 ⚒️ <span style={{ color: 'var(--ink-1)' }}>炼因子</span> tab 用 DSL 炼一个自己的</> : <>试试其它 family · 或切到 ⚒️ <span style={{ color: 'var(--ink-1)' }}>炼因子</span> 炼一个 user 因子</>}
              </div>
            </div>
          )}
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

// ═════════════════════════ SP-W2C 节点 Tooltip ═════════════════════════
// 悬停工具栏节点 → 浮层显示完整 metadata (description / inputs / outputs / risk / pit / tag).
// 不弹 modal, 不蹦走鼠标点击 — onMouseEnter/Leave 控显隐, position:fixed 跟鼠标走.
// 防截屏遮挡: 右侧空间不够时翻到左侧 (依 anchor.right < window.innerWidth - 380).
function NodeTooltip({ node, anchor }) {
  if (!node) return null;
  // 选位置: anchor 右侧 +8px, 顶端对齐 anchor.top, 若右侧空间不够则反弹到左侧
  const winW = (typeof window !== 'undefined') ? window.innerWidth : 1400;
  const w = 360;
  const left = (anchor && (anchor.right + 8 + w < winW)) ? anchor.right + 8 : (anchor ? Math.max(8, anchor.left - w - 8) : 8);
  const top = anchor ? Math.max(8, anchor.top) : 60;
  const inputsSchema = node.params_schema || {};
  const inputProps = inputsSchema.properties || {};
  const inputRequired = new Set(inputsSchema.required || []);
  const outputsSchema = node.outputs_schema || {};
  const outputProps = outputsSchema.properties || {};
  return (
    <div style={{
      position: 'fixed', left, top, width: w, maxHeight: '70vh', overflowY: 'auto', zIndex: 50,
      padding: '12px 14px', background: 'var(--paper)', border: '1px solid var(--ink-3)',
      boxShadow: '0 4px 16px rgba(28,24,20,0.12)', pointerEvents: 'none',
      fontFamily: 'var(--serif)',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
        <code className="mono" style={{ fontSize: 13, color: 'var(--ink)' }}>{node.type}</code>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.14em' }}>{(node.group || 'misc').toUpperCase()}</span>
      </div>
      {node.description && (
        <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-1)', lineHeight: 1.55, marginBottom: 8 }}>{node.description}</div>
      )}
      {/* meta strip: risk / pit / tag */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
        {node.risk && node.risk !== 'normal' && (
          <span className="mono" style={{ fontSize: 9, padding: '2px 6px', background: 'rgba(180,80,60,0.08)', color: 'var(--yin)', border: '1px solid rgba(180,80,60,0.18)', letterSpacing: '0.1em' }}>RISK: {node.risk}</span>
        )}
        {node.pit && (
          <span className="mono" style={{ fontSize: 9, padding: '2px 6px', background: 'rgba(180,140,60,0.08)', color: 'var(--jin)', border: '1px solid rgba(180,140,60,0.18)', letterSpacing: '0.1em' }}>PIT</span>
        )}
        {(node.tag || []).map(t => (
          <span key={t} className="mono" style={{ fontSize: 9, padding: '2px 6px', background: 'rgba(28,24,20,0.04)', color: 'var(--ink-2)', border: '1px solid var(--line-soft)', letterSpacing: '0.1em' }}>#{t}</span>
        ))}
      </div>
      {/* params schema summary */}
      {Object.keys(inputProps).length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.14em', marginBottom: 4 }}>PARAMS</div>
          {Object.entries(inputProps).map(([k, v]) => (
            <div key={k} className="mono" style={{ fontSize: 11, color: 'var(--ink-1)', marginBottom: 2 }}>
              <span style={{ color: 'var(--ink)' }}>{k}</span>
              <span style={{ color: 'var(--ink-3)' }}>: {v.type || 'any'}</span>
              {inputRequired.has(k) && <span style={{ color: 'var(--yin)', marginLeft: 4 }}>*</span>}
              {v.default !== undefined && <span style={{ color: 'var(--ink-3)' }}> = {JSON.stringify(v.default)}</span>}
              {v.description && <div className="serif" style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 12, marginTop: 1, lineHeight: 1.45 }}>{v.description}</div>}
            </div>
          ))}
        </div>
      )}
      {Object.keys(inputProps).length === 0 && (
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginBottom: 8 }}>无 params</div>
      )}
      {/* outputs schema summary */}
      {Object.keys(outputProps).length > 0 && (
        <div>
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.14em', marginBottom: 4 }}>OUTPUTS</div>
          {Object.entries(outputProps).map(([k, v]) => (
            <div key={k} className="mono" style={{ fontSize: 11, color: 'var(--ink-1)', marginBottom: 2 }}>
              <span style={{ color: 'var(--ink)' }}>{k}</span>
              <span style={{ color: 'var(--ink-3)' }}>: {v.type || 'any'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ═════════════════════════ SP-W2B Copilot 栏 ═════════════════════════
// 输入自然语言 → POST /workflow/copilot/draft (SSE) → 展示 thought/draft → 点 [✓ 用这个] 加载到画板
//
// SSE on POST: EventSource 只支持 GET, 这里用 fetch + ReadableStream.getReader 手动解析 SSE 帧.
// 协议: event: <name>\ndata: <json>\n\n
function CopilotBar({ onDraftAccept }) {
  const [goal, setGoal] = useState('');
  const [universe, setUniverse] = useState('csi300_active');
  const [running, setRunning] = useState(false);
  const [thoughts, setThoughts] = useState([]);    // [{text}]
  const [draft, setDraft] = useState(null);        // {workflow_json, cited_experiences, risk_flags, used_factors}
  const [error, setError] = useState('');
  const abortRef = useRef(null);

  // 解 SSE 流 — fetch ReadableStream → 行分块 → {event, data} 数组
  // 复用最简单实现: buffer 行, 见 \n\n 切帧
  const consumeSSE = async (resp, onEvent) => {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (!frame.trim() || frame.startsWith(':')) continue;
        const evMatch = frame.match(/^event:\s*(.+)$/m);
        const dtMatch = frame.match(/^data:\s*(.+)$/m);
        if (!evMatch || !dtMatch) continue;
        let data = null;
        try { data = JSON.parse(dtMatch[1]); } catch (e) { data = { _raw: dtMatch[1] }; }
        onEvent(evMatch[1].trim(), data);
      }
    }
  };

  const go = async () => {
    if (!goal.trim() || running) return;
    // 关上一个 (复点 [Go] 时)
    if (abortRef.current) { try { abortRef.current.abort(); } catch (e) {} }
    setRunning(true); setThoughts([]); setDraft(null); setError('');
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const resp = await fetch(API + '/workflow/copilot/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal, universe, freq: 'day' }),
        signal: ctrl.signal,
      });
      if (!resp.ok) {
        setError('HTTP ' + resp.status);
        setRunning(false);
        return;
      }
      await consumeSSE(resp, (event, data) => {
        if (event === 'thought') {
          setThoughts(ts => [...ts, { text: data.text || '' }]);
        } else if (event === 'draft') {
          setDraft(data);
        } else if (event === 'error') {
          setError(data.message || 'Copilot 出错');
        } else if (event === 'done') {
          // 流终止
        }
      });
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message || String(e));
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  };

  const accept = () => {
    if (!draft || !draft.workflow_json) return;
    onDraftAccept(draft);
    // 清空草稿区, 保留 thoughts 给用户回顾
    setDraft(null);
  };

  const reset = () => {
    if (abortRef.current) { try { abortRef.current.abort(); } catch (e) {} abortRef.current = null; }
    setRunning(false); setThoughts([]); setDraft(null); setError('');
  };

  // 卸载清理
  useEffect(() => () => { if (abortRef.current) { try { abortRef.current.abort(); } catch (e) {} } }, []);

  return (
    <div style={{ borderBottom: '1px solid var(--line)', background: 'rgba(255,247,225,0.55)' }}>
      {/* 输入栏 */}
      <div style={{ padding: '10px 18px 8px', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)', letterSpacing: '0.14em' }}>AI 代搭</span>
        <input type="text" value={goal} onChange={e => setGoal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !running) go(); }}
          placeholder="让 AI 代搭, 例: 用反转因子在 csi300 跑 IC"
          style={{ flex: 1, minWidth: 280, padding: '6px 10px', border: '1px solid var(--line)', fontFamily: 'var(--serif)', fontSize: 13, background: 'var(--paper)' }} />
        <select value={universe} onChange={e => setUniverse(e.target.value)}
          style={{ padding: '5px 8px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 11, background: 'var(--paper)' }}>
          <option value="csi_fast">快测(~100)</option>
          <option value="csi300_active">csi300_active</option>
          <option value="csi500">csi500</option>
          <option value="csi800">csi800</option>
          <option value="all">all</option>
        </select>
        <button onClick={go} disabled={!goal.trim() || running}
          className="hover-pill" style={{ padding: '6px 16px', border: 'none', background: (!goal.trim() || running) ? 'var(--line)' : 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: (!goal.trim() || running) ? 'default' : 'pointer' }}>
          {running ? '思考中…' : 'Go ▶'}
        </button>
        {(thoughts.length > 0 || draft || error) && (
          <button onClick={reset} className="hover-pill" style={{ padding: '4px 10px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontFamily: 'var(--serif)', fontSize: 11 }}>清空</button>
        )}
      </div>

      {/* 推理流 panel (有事件才显示) */}
      {(thoughts.length > 0 || draft || error || running) && (
        <div style={{ padding: '4px 18px 12px', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
          {/* 左: thoughts 流 */}
          <div style={{ flex: 1, minWidth: 0, maxHeight: 200, overflowY: 'auto', padding: '8px 12px', background: 'rgba(28,24,20,0.04)', border: '1px solid var(--line-soft)' }}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.14em', marginBottom: 4 }}>RECONSTRUCT · {thoughts.length} 段思考</div>
            {thoughts.length === 0 && running && <span className="serif" style={{ color: 'var(--ink-3)', fontSize: 12 }}>等 LLM…</span>}
            {thoughts.map((t, i) => (
              <div key={i} className="serif" style={{ fontSize: 11.5, color: 'var(--ink-1)', marginBottom: 4, lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{t.text}</div>
            ))}
            {error && <div style={{ marginTop: 6 }}><ErrorBox error={error} /></div>}
          </div>

          {/* 右: draft 卡 */}
          {draft && draft.workflow_json && (
            <div style={{ flex: '0 0 360px', padding: '10px 14px', background: 'var(--paper)', border: '1px solid var(--ink-3)', boxShadow: '0 2px 8px rgba(28,24,20,0.06)' }}>
              <div className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', letterSpacing: '0.14em', marginBottom: 6 }}>DRAFT · {(draft.workflow_json.nodes || []).length} 节点</div>
              <div className="serif" style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 500, marginBottom: 6 }}>{draft.workflow_json.name || '未命名'}</div>
              {/* 节点链 */}
              <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-1)', marginBottom: 6, lineHeight: 1.7 }}>
                {(draft.workflow_json.nodes || []).map((n, i) => (
                  <div key={i}>{i + 1}. <span style={{ color: 'var(--ink)' }}>{n.id}</span> <span style={{ color: 'var(--ink-3)' }}>({n.type})</span></div>
                ))}
              </div>
              {/* 引用经验 */}
              {(draft.cited_experiences || []).length > 0 && (
                <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid var(--line-soft)' }}>
                  <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.14em' }}>引用经验</div>
                  {draft.cited_experiences.map((c, i) => (
                    <div key={i} className="serif" style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 2 }}>· {c.source}{c.section ? ' § ' + c.section : ''}</div>
                  ))}
                </div>
              )}
              {/* 风险提示 */}
              {(draft.risk_flags || []).length > 0 && (
                <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid var(--line-soft)' }}>
                  <div className="mono" style={{ fontSize: 9, color: 'var(--yin)', letterSpacing: '0.14em' }}>风险</div>
                  {draft.risk_flags.map((r, i) => (
                    <div key={i} className="serif" style={{ fontSize: 10.5, color: 'var(--yin)', marginTop: 2 }}>! {r}</div>
                  ))}
                </div>
              )}
              {/* used_factors */}
              {(draft.used_factors || []).length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.14em' }}>用到因子</div>
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-1)', marginTop: 2 }}>{draft.used_factors.join(', ')}</div>
                </div>
              )}
              {/* Actions */}
              <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
                <button onClick={accept} className="hover-pill"
                  style={{ flex: 1, padding: '6px 12px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 11, cursor: 'pointer' }}>
                  ✓ 用这个
                </button>
                <button onClick={() => setDraft(null)} className="hover-pill"
                  style={{ padding: '6px 12px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontFamily: 'var(--serif)', fontSize: 11, color: 'var(--ink-2)' }}>
                  ✗ 重来
                </button>
              </div>
            </div>
          )}
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
  // SP-W2C: hover tooltip 显示节点 schema 详情 (描述 + inputs + outputs + risk/pit/tag)
  const [hoverNode, setHoverNode] = useState(null);             // RegisteredNode payload (or null)
  const [hoverAnchor, setHoverAnchor] = useState(null);         // DOMRect of hovered toolbar row
  // SP-W2C: node_done 事件累 artifact_uri 给 step list 行的 "查看输出" 按钮显示判定
  const [nodeArtifacts, setNodeArtifacts] = useState({});       // {node_id: 1}  存在 = 有 artifact
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

  // SP-W2B: Copilot 草稿 → 加到画板. draft.workflow_json 是 {name, nodes, edges?, meta?}
  // 直接拼进 currentWorkflow, wf_id 留 null (用户得点 Save 才落盘)
  const loadDraftToCanvas = (draft) => {
    const wf = (draft && draft.workflow_json) || {};
    setCurrentWorkflow({
      wf_id: null,
      name: wf.name || 'AI 草案',
      nodes: wf.nodes || [],
      edges: wf.edges || [],
      meta: wf.meta || {},
    });
    setSelectedNodeIdx(null);
    setNodeRunStatus({});
    setNodeArtifacts({});
    setRunEvents([]);
    setRunId(null);
    setRunStatus('idle');
    setArtifactView(null);
    setSaveErr('');
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
      setNodeArtifacts({});
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
    setRunEvents([]); setNodeRunStatus({}); setNodeArtifacts({}); setRunStatus('running'); setArtifactView(null); setSaveErr('');
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
          // SP-W2C: 记 artifact_uri 存在, step list 行才显示 "查看输出"
          if (data.artifact_uri) {
            setNodeArtifacts(a => ({ ...a, [data.node_id]: data.artifact_uri }));
          }
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
      {/* SP-W2B Copilot 栏 — AI 代搭工作流 (固定在顶部, 工具条之上) */}
      <CopilotBar onDraftAccept={loadDraftToCanvas} />

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
        <button onClick={() => { setCurrentWorkflow({ wf_id: null, name: '未命名工作流', nodes: [], edges: [], meta: {} }); setSelectedNodeIdx(null); setRunEvents([]); setNodeRunStatus({}); setNodeArtifacts({}); setRunId(null); setRunStatus('idle'); setArtifactView(null); }}
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
              <div className="mono" style={{ fontSize: 10, padding: '6px 12px 4px', color: 'var(--ink-2)', letterSpacing: '0.14em', background: 'rgba(28,24,20,0.04)' }}>{grp.toUpperCase()} <span style={{ color: 'var(--ink-3)' }}>· {groupedNodes[grp].length}</span></div>
              {groupedNodes[grp].map(n => (
                <div key={n.type} className="hover-row" onClick={() => addNode(n)}
                  data-node-type={n.type}
                  onMouseEnter={e => { setHoverNode(n); setHoverAnchor(e.currentTarget.getBoundingClientRect()); }}
                  onMouseLeave={() => { setHoverNode(null); setHoverAnchor(null); }}
                  style={{ padding: '7px 12px', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                    <code className="mono" style={{ fontSize: 11.5, color: 'var(--ink)' }}>{(n.type.split('.')[1] || n.type)}</code>
                    {n.risk && n.risk !== 'normal' && <span className="mono" style={{ fontSize: 8.5, color: 'var(--yin)', letterSpacing: '0.1em' }}>RISK</span>}
                    {n.pit && <span className="mono" style={{ fontSize: 8.5, color: 'var(--jin)', letterSpacing: '0.1em' }}>PIT</span>}
                  </div>
                  <div className="serif" style={{ fontSize: 10, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.description || '—'}</div>
                </div>
              ))}
            </div>
          ))}
        </aside>
        {/* SP-W2C: 节点 hover tooltip — position:fixed 跟随鼠标 anchor */}
        {hoverNode && hoverAnchor && <NodeTooltip node={hoverNode} anchor={hoverAnchor} />}

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
          {/* SP-W2C: 工作流描述 (meta.description) — demo seed 用 */}
          {currentWorkflow.meta && currentWorkflow.meta.description && (
            <div className="serif" style={{ padding: '8px 14px', fontSize: 11, color: 'var(--ink-3)', background: 'rgba(255,247,225,0.4)', borderBottom: '1px solid var(--line-soft)', lineHeight: 1.5 }}>
              <span className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', letterSpacing: '0.14em' }}>DESC </span>
              {currentWorkflow.meta.description}
            </div>
          )}
          <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0', minHeight: 0 }}>
            {!currentWorkflow.nodes.length && (
              <div style={{ padding: 24 }}>
                <Empty label="未添加节点" />
                <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', marginTop: 12, lineHeight: 1.6 }}>
                  👈 从左侧工具栏点节点添加<br />
                  🤖 或上方 <span style={{ color: 'var(--ink-1)' }}>AI 代搭</span> 描述目标让 Copilot 出草案
                </div>
              </div>
            )}
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
                    {status && status !== 'running' && nodeArtifacts[n.id] && (
                      <button onClick={e => { e.stopPropagation(); viewArtifact(n.id); }}
                        title={'artifact_uri: ' + nodeArtifacts[n.id]}
                        className="hover-pill" style={{ padding: '2px 8px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: 10.5, color: 'var(--ink-1)' }}>📊 查看输出</button>
                    )}
                    {status && status !== 'running' && !nodeArtifacts[n.id] && (
                      <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }} title="该节点无输出 artifact">—</span>
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
          {!selectedNode && (
            <div style={{ padding: 24 }}>
              <Empty label="选中 Step List 节点编辑参数" />
              {currentWorkflow.nodes.length === 0 && (
                <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', marginTop: 12, lineHeight: 1.6 }}>
                  暂无节点 · 从左侧工具栏点节点添加, 或上方 <span style={{ color: 'var(--ink-1)' }}>AI 代搭</span> 让 Copilot 出草案
                </div>
              )}
            </div>
          )}
          {selectedNode && (
            <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* SP-W2C: 节点 metadata 横条 (type / group / tag / risk / pit) — 跟工具栏分组对齐 */}
              {selectedNodeMeta && (
                <div style={{ padding: '8px 10px', background: 'rgba(28,24,20,0.04)', border: '1px solid var(--line-soft)', display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.14em' }}>TYPE</span>
                  <code className="mono" style={{ fontSize: 11, color: 'var(--ink)' }}>{selectedNodeMeta.type}</code>
                  <span style={{ flex: 1 }} />
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.14em' }}>GROUP</span>
                  <span className="mono" style={{ fontSize: 10, padding: '1px 6px', color: 'var(--ink-1)', background: 'var(--paper)', border: '1px solid var(--line-soft)' }}>{selectedNodeMeta.group || 'misc'}</span>
                  {selectedNodeMeta.risk && selectedNodeMeta.risk !== 'normal' && (
                    <span className="mono" style={{ fontSize: 9, padding: '1px 5px', color: 'var(--yin)', background: 'rgba(180,80,60,0.06)', border: '1px solid rgba(180,80,60,0.18)' }}>RISK: {selectedNodeMeta.risk}</span>
                  )}
                  {selectedNodeMeta.pit && (
                    <span className="mono" style={{ fontSize: 9, padding: '1px 5px', color: 'var(--jin)', background: 'rgba(180,140,60,0.06)', border: '1px solid rgba(180,140,60,0.18)' }}>PIT</span>
                  )}
                  {(selectedNodeMeta.tag || []).length > 0 && (
                    <div style={{ width: '100%', display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                      {(selectedNodeMeta.tag || []).map(t => (
                        <span key={t} className="mono" style={{ fontSize: 9, padding: '1px 5px', color: 'var(--ink-2)', background: 'var(--paper)', border: '1px solid var(--line-soft)' }}>#{t}</span>
                      ))}
                    </div>
                  )}
                </div>
              )}
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

// ─────── BacktestStrategyBanner — Mock vs Real 说清楚回测什么 (P0.1) ───────
function BacktestStrategyBanner({ mode }) {
  if (mode === 'mock') {
    return (
      <div style={{
        padding: '12px 16px', marginBottom: 12,
        border: '1px solid var(--line)', background: 'var(--paper-1)',
        borderLeft: '3px solid var(--dai)', fontSize: 12,
      }}>
        <div className="serif" style={{ fontSize: 13, color: 'var(--ink)', marginBottom: 6 }}>
          📊 <strong>Mock 模式</strong> · 演示数据通路, <span style={{ color: 'var(--dai)' }}>⚠ 不是盈利策略</span>
        </div>
        <div style={{ color: 'var(--ink-2)', lineHeight: 1.7 }}>
          每次空仓时买入候选池中 <code className="mono">rev_20</code> 分位最低 (跌得最惨) 的 1 只,
          持有 <code className="mono">N</code> 个交易日后无条件了结. 0 次 LLM 调用, 确定性, 可手算核对.
        </div>
        <div style={{ color: 'var(--ink-3)', fontSize: 11, marginTop: 6 }}>
          用途: 验证 数据→决策→撮合→净值 链路通畅. 真实策略请切 <strong>Real LLM</strong> 模式.
        </div>
      </div>
    );
  }
  return (
    <div style={{
      padding: '12px 16px', marginBottom: 12,
      border: '1px solid var(--line)', background: 'var(--paper-1)',
      borderLeft: '3px solid var(--zhu)', fontSize: 12,
    }}>
      <div className="serif" style={{ fontSize: 13, color: 'var(--ink)', marginBottom: 6 }}>
        🤖 <strong>Real LLM</strong> · 真实策略回测 (慢, 单日窗口 ~6min)
      </div>
      <div style={{ color: 'var(--ink-2)', lineHeight: 1.7 }}>
        每日盘前调用 qwen3.5-plus, 输入: 候选池 Top-N · 当前持仓 · rev_20 分位 · 当日新闻 + 事件摘要 (PIT-safe).
        输出 5 档动作 (buy/add/hold/reduce/sell), 每条带 reason.
      </div>
      <div style={{ color: 'var(--ink-3)', fontSize: 11, marginTop: 6 }}>
        决策被 prompt 哈希缓存 — 同样输入只调一次 LLM (<code className="mono">.fa/decision_cache</code>).
      </div>
    </div>
  );
}

// ─────── BacktestSummaryChips — 候选池+因子+窗口+持有期 一行 chip 串 (P0.2) ───────
function BacktestSummaryChips({ d, onPoolClick }) {
  const p = d.params || {};
  const tradeDays = d.nav && d.nav.dates ? d.nav.dates.length : '?';
  const factorLabel = p.factor_name || 'rev_20';
  // codes 模式 (2026-06-03): 显示 "指定代码 (N 只: code1, code2, ...)" 替代 "池: csi300"
  const isCodesMode = Array.isArray(p.codes) && p.codes.length > 0;
  const poolLabel = isCodesMode
    ? `指定代码 (${p.codes.length} 只: ${p.codes.slice(0, 3).join(', ')}${p.codes.length > 3 ? '...' : ''})`
    : (p.pool || '(旧 watchlist 模式)');
  const modeLabel = p.mode === 'mock' ? 'Mock' : 'Real LLM';
  return (
    <div style={{
      padding: '10px 14px', marginBottom: 14,
      border: '1px solid var(--line-soft)', background: 'var(--paper-2)',
      display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12,
      fontSize: 11, fontFamily: 'var(--mono)',
    }}>
      <span style={{ color: 'var(--ink-2)' }}>候选 N=<strong>{p.candidate_topn}</strong></span>
      <span style={{ color: 'var(--ink-3)' }}>◀</span>
      <span onClick={onPoolClick} style={{
        cursor: 'pointer', textDecoration: 'underline dotted', color: 'var(--ink)',
      }} title={isCodesMode ? '点开看用户指定 codes 详情' : '点开看候选池过滤逻辑'}>
        {isCodesMode ? '候选' : '池'}: <strong>{poolLabel}</strong>
      </span>
      <span style={{ color: 'var(--ink-3)' }}>◀</span>
      <span style={{ color: 'var(--ink-2)' }}>排序: <code>{factorLabel}</code> ↑</span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: 'var(--ink-2)' }}>窗口: <strong>{p.start}</strong> → <strong>{p.end}</strong> ({tradeDays} 个交易日)</span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: 'var(--ink-2)' }}>模式: <strong>{modeLabel}</strong></span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: 'var(--ink-2)' }}>持有: <strong>{p.hold_days || 3} 日</strong></span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: 'var(--ink-2)' }}>撮合: <strong>{p.match_freq}</strong></span>
    </div>
  );
}

// ─────── PoolFilterPopover — 池过滤逻辑浮层 (P1.3) ───────
// stats: { n_pool, n_holdings, n_base, n_rev20_computable, n_final } 来自后端
// CandidateResult.filter_stats (末日快照). 缺则显示 '?', n_final 退 topn.
// codes 模式 (2026-06-03): 传 codes 非空时切 5 行 codes 流程 (不走池子过滤).
function PoolFilterPopover({ pool, codes, topn, stats, onClose }) {
  const isCodesMode = Array.isArray(codes) && codes.length > 0;
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(20,20,20,0.4)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        maxWidth: 540, padding: 24, background: 'var(--paper)', border: '1px solid var(--line)',
      }}>
        <div className="serif" style={{ fontSize: 14, marginBottom: 12 }}>
          {isCodesMode ? (
            <>用户指定候选构造流程 · <code className="mono">codes ({codes.length} 只)</code></>
          ) : (
            <>候选池构造流程 · 当前 <code className="mono">{pool}</code></>
          )}
        </div>
        {isCodesMode ? (
          <ol className="mono" style={{ fontSize: 11.5, lineHeight: 1.9, color: 'var(--ink-2)', paddingLeft: 22 }}>
            <li>用户指定 codes (<strong>{codes.length}</strong> 只): <code style={{ fontSize: 11 }}>{codes.slice(0, 5).join(', ')}{codes.length > 5 ? ` ... +${codes.length - 5}` : ''}</code></li>
            <li>叠加当前持仓 ({stats?.n_holdings ?? 0} 只, 避免持仓掉出候选导致无法平仓)</li>
            <li>合并去重 → base universe ({stats?.n_base ?? '?'} 只)</li>
            <li>对每只在 ≤T-1 close 上算 <code>rev_20</code> 排名信息 (可算 <strong>{stats?.n_rev20_computable ?? '?'}</strong> 只)</li>
            <li>不走池子过滤, 用户 codes 全部入选 (实际 <strong>{stats?.n_final ?? codes.length}</strong> 只)</li>
          </ol>
        ) : (
          <ol className="mono" style={{ fontSize: 11.5, lineHeight: 1.9, color: 'var(--ink-2)', paddingLeft: 22 }}>
            <li>全 <strong>{pool}</strong> 成分股 ({stats?.n_pool ?? '?'} 只, 来自 <code>stock_data/parquet/index_constituents.parquet</code>)</li>
            <li>叠加当前持仓 ({stats?.n_holdings ?? 0} 只, 避免持仓掉出候选导致无法平仓)</li>
            <li>排除 sentinel (SH999999 等占位代码)</li>
            <li>合并去重 → base universe ({stats?.n_base ?? '?'} 只)</li>
            <li>对每只在 ≤T-1 close 上算 <code>rev_20 = close[T-1]/close[T-21] - 1</code> (要求 ≥21 close 点, 满足 <strong>{stats?.n_rev20_computable ?? '?'}</strong> 只)</li>
            <li>按 rev_20 <strong>升序</strong> 取前 N=<strong>{topn}</strong> (跌得最惨的优先)</li>
          </ol>
        )}
        <div className="mono" style={{ fontSize: 11.5, marginTop: 10, color: 'var(--ink-1)' }}>
          实际入选 <strong>{stats?.n_final ?? (isCodesMode ? codes.length : topn)}</strong> 只
          {isCodesMode ? ' (用户 codes ∪ 持仓, 去重)' : ' (rev_20 Top-N ∪ 持仓, 去重)'}
        </div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 12 }}>
          {isCodesMode
            ? '注: codes 模式 (用户指定代码) 不走池子过滤, 也不引入 watchlist; 优先级 codes > pool > watchlist. 数字为窗口末日快照.'
            : '注: 池子模式 (pool 非空) 不引入 watchlist; 老 WatchLoop 实盘盯盘仍走 holdings∪watchlist 路径. 数字为窗口末日快照.'}
        </div>
        <button onClick={onClose} style={{
          marginTop: 16, padding: '6px 14px', background: 'var(--ink)', color: 'var(--paper)',
          border: 'none', fontSize: 11, cursor: 'pointer', fontFamily: 'var(--serif)',
        }}>关闭</button>
      </div>
    </div>
  );
}

// ─────── Section — modal 小标题 + 分隔线 ───────
function BacktestModalSection({ title, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="mono" style={{
        fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.15em',
        textTransform: 'uppercase', marginBottom: 6, paddingBottom: 4, borderBottom: '1px solid var(--line-soft)',
      }}>{title}</div>
      <div>{children}</div>
    </div>
  );
}

// ─────── TradeReasonModal — 交易理由可点击展开 (P0.3 + P1.2) ───────
function TradeReasonModal({ trade, d, onClose }) {
  const [rawExpanded, setRawExpanded] = useState(false);
  if (!trade || !d) return null;
  const day = (d.decisions && d.decisions[trade.date]) || {};
  const legs = day.decisions || [];
  const marketView = day.market_view || '—';
  const raw = day.raw || null;
  const warnings = day.warnings || [];
  const isMock = (d.mode || (d.params && d.params.mode)) === 'mock';
  const rawStr = raw ? JSON.stringify(raw, null, 2) : null;
  const rawPreview = rawStr && rawStr.length > 200 ? rawStr.slice(0, 200) + '…' : rawStr;
  const rawHasError = raw && raw._error === 'json';
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(20,20,20,0.5)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        maxWidth: 680, width: '100%', maxHeight: '85vh', overflow: 'auto',
        padding: 22, background: 'var(--paper)', border: '1px solid var(--line)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14 }}>
          <span className="serif" style={{ fontSize: 14 }}>
            {trade.date} · <code className="mono">{trade.code}</code> · <span style={{
              color: trade.action === 'buy' ? 'var(--zhu)' : 'var(--dai)',
            }}>{trade.action}</span>
          </span>
          <button onClick={onClose} style={{
            border: 'none', background: 'transparent', fontSize: 18, cursor: 'pointer', color: 'var(--ink-3)',
          }}>×</button>
        </div>

        {rawHasError && (
          <div style={{ padding: 8, marginBottom: 12, background: '#fff5e6', border: '1px solid var(--jin)', fontSize: 11 }}>
            ⚠ LLM 输出非合法 JSON, 已 fallback (原始文本见 <code>raw._raw</code>)
          </div>
        )}

        <BacktestModalSection title="当日 market_view">
          <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.7 }}>{marketView}</div>
        </BacktestModalSection>

        <BacktestModalSection title="本笔 reason">
          <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.7 }}>
            {trade.reason || (legs.find(l => l.code === trade.code) || {}).reason || '—'}
          </div>
        </BacktestModalSection>

        <BacktestModalSection title={`当日全部决策 (${legs.length} 条)`}>
          {legs.length === 0 ? <span style={{ color: 'var(--ink-3)', fontSize: 11 }}>—</span> : (
            <ol style={{ paddingLeft: 18, margin: 0 }}>
              {legs.map((l, i) => (
                <li key={i} className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginBottom: 4 }}>
                  [{i + 1}] <span style={{ color: l.action === 'buy' ? 'var(--zhu)' : 'var(--dai)' }}>{l.action}</span>
                  {' '}<code>{l.code}</code> {l.weight_pct ? `${l.weight_pct}%` : ''} stop={l.stop_loss}
                  <div style={{ paddingLeft: 18, color: 'var(--ink-3)', fontFamily: 'var(--serif)' }}>{l.reason}</div>
                </li>
              ))}
            </ol>
          )}
        </BacktestModalSection>

        {!isMock && rawStr && (
          <BacktestModalSection title="LLM 返回原文 (raw JSON)">
            <pre className="mono" style={{
              fontSize: 10.5, padding: 10, background: 'var(--paper-2)', border: '1px solid var(--line-soft)',
              whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
            }}>{rawExpanded ? rawStr : rawPreview}</pre>
            {rawStr.length > 200 && (
              <button onClick={() => setRawExpanded(!rawExpanded)} style={{
                marginTop: 6, border: 'none', background: 'transparent', color: 'var(--zhu)',
                fontSize: 10.5, cursor: 'pointer', fontFamily: 'var(--mono)',
              }}>{rawExpanded ? '收起 ▴' : '展开看全文 ▾'}</button>
            )}
          </BacktestModalSection>
        )}

        {warnings.length > 0 && (
          <BacktestModalSection title="当日警告">
            <ul style={{ paddingLeft: 18, fontSize: 11, color: 'var(--jin)' }}>
              {warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </BacktestModalSection>
        )}
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
  // P0/P1/P2: 新增 UI state (modal + popover + 高级控件)
  const [showPoolPopover, setShowPoolPopover] = useState(false);
  const [selectedTrade, setSelectedTrade] = useState(null);
  const [showAdv, setShowAdv] = useState(false);
  const [pool, setPool] = useState('csi300');
  const [holdDays, setHoldDays] = useState(3);
  const [factorName, setFactorName] = useState('rev_20');
  const [stopLossEnabled, setStopLossEnabled] = useState(false);
  const [stopLossPct, setStopLossPct] = useState(0.05);
  const [takeProfitEnabled, setTakeProfitEnabled] = useState(false);
  const [takeProfitPct, setTakeProfitPct] = useState(0.1);
  // codes 模式 (2026-06-03): 候选模式切换 — 'pool' (现有) | 'codes' (单股/watchlist)
  const [candidateMode, setCandidateMode] = useState('pool');
  const [codesInput, setCodesInput] = useState('');

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
    // codes 模式 (2026-06-03): 解析逗号/空格/中文逗号/顿号分隔的 codes, trim+大写+去空
    // 非空时透传 codes 并自动 topn=len(codes); pool 仍传默认占位但后端忽略
    const codesList = candidateMode === 'codes'
      ? codesInput.split(/[\s,，、]+/).map(s => s.trim().toUpperCase()).filter(Boolean)
      : null;
    run.run(async () => {
      const r = await postJSON('/backtest/run', {
        start: start || null, end: end || null,
        init_cash: Number(cash),
        candidate_topn: (codesList && codesList.length) ? codesList.length : Number(topn),
        mode,
        match_freq: 'day',
        // P2 ↓ 新增字段
        pool, hold_days: Number(holdDays), factor_name: factorName,
        stop_loss_pct: stopLossEnabled ? stopLossPct : null,
        take_profit_pct: takeProfitEnabled ? takeProfitPct : null,
        // codes 模式 (2026-06-03)
        codes: (codesList && codesList.length) ? codesList : null,
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
        <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}
               title={candidateMode === 'codes' ? '代码模式: N 自动 = len(codes)' : '候选池 Top-N'}>候选 N
          <input type="number" value={topn} onChange={e => setTopn(e.target.value)}
            disabled={candidateMode === 'codes'}
            style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: 70, border: '1px solid var(--line)',
                     fontFamily: 'var(--mono)', fontSize: 12,
                     opacity: candidateMode === 'codes' ? 0.4 : 1 }} /></label>
        <Segmented value={mode} onChange={setMode}
          options={[{ value: 'mock', label: 'Mock(秒级)' }, { value: 'real', label: '真 LLM(慢)' }]} />
        <button onClick={start_run} disabled={run.loading || polling} className="hover-pill"
          style={{ padding: '7px 16px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
          {(run.loading || polling) ? '回测中…' : '起回测 ▶'}
        </button>
        <button onClick={() => setShowAdv(!showAdv)} className="hover-pill"
          style={{ padding: '6px 12px', border: '1px solid var(--line)', background: 'transparent',
                   fontFamily: 'var(--serif)', fontSize: 11, cursor: 'pointer' }}>
          高级 {showAdv ? '▴' : '▾'}
        </button>
      </div>

      {showAdv && (
        <div style={{
          display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap',
          padding: '10px 12px', marginBottom: 12, border: '1px solid var(--line-soft)', background: 'var(--paper-1)',
        }}>
          {/* codes 模式 (2026-06-03): 候选模式切换 — 池子 (现有) | 指定代码 (单股/watchlist) */}
          <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>候选模式
            <div style={{ marginTop: 3 }}>
              <Segmented value={candidateMode} onChange={setCandidateMode}
                options={[{ value: 'pool', label: '池子' }, { value: 'codes', label: '指定代码' }]} />
            </div>
          </label>
          {candidateMode === 'codes' ? (
            <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', flex: 1, minWidth: 280 }}
                   title="逗号/空格分隔, 格式 ^(SH|SZ|BJ)\d{6}$, ≤50 只">
              候选代码 (逗号/空格分隔, topn 自动)
              <input value={codesInput} onChange={e => setCodesInput(e.target.value)}
                placeholder="SH600519, SZ002594, SH601318"
                style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: '100%',
                         border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12 }} />
            </label>
          ) : (
            <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>候选池
              <select value={pool} onChange={e => setPool(e.target.value)}
                style={{ display: 'block', marginTop: 3, padding: '5px 8px', border: '1px solid var(--line)',
                         fontFamily: 'var(--mono)', fontSize: 12 }}>
                <option value="csi300">csi300 (300 只)</option>
                <option value="csi_fast">csi_fast (~100 大盘)</option>
                <option value="csi500">csi500 (500 只)</option>
                <option value="csi800">csi800 (800 只)</option>
              </select></label>
          )}
          <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>持有期 (日)
            <input type="number" min={1} max={60} value={holdDays}
              onChange={e => setHoldDays(Number(e.target.value))}
              style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: 70,
                       border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12 }} /></label>
          <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}
                 title="第一版只支持 rev_20, 其它因子下轮接 /factor/list">
            排序因子
            <select value={factorName} disabled
              style={{ display: 'block', marginTop: 3, padding: '5px 8px', border: '1px solid var(--line)',
                       fontFamily: 'var(--mono)', fontSize: 12, opacity: 0.6 }}>
              <option value="rev_20">rev_20 (反转)</option>
            </select></label>
          <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={stopLossEnabled} onChange={e => setStopLossEnabled(e.target.checked)} />
            止损 %
            <input type="number" min={1} max={50} step={1} disabled={!stopLossEnabled}
              value={Math.round(stopLossPct * 100)}
              onChange={e => setStopLossPct(Number(e.target.value) / 100)}
              style={{ width: 50, padding: '4px 6px', border: '1px solid var(--line)',
                       fontFamily: 'var(--mono)', fontSize: 11, opacity: stopLossEnabled ? 1 : 0.4 }} />
          </label>
          <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={takeProfitEnabled} onChange={e => setTakeProfitEnabled(e.target.checked)} />
            止盈 %
            <input type="number" min={1} max={200} step={1} disabled={!takeProfitEnabled}
              value={Math.round(takeProfitPct * 100)}
              onChange={e => setTakeProfitPct(Number(e.target.value) / 100)}
              style={{ width: 50, padding: '4px 6px', border: '1px solid var(--line)',
                       fontFamily: 'var(--mono)', fontSize: 11, opacity: takeProfitEnabled ? 1 : 0.4 }} />
          </label>
        </div>
      )}

      <BacktestStrategyBanner mode={mode} />

      {(run.loading || polling) && <Loading label={mode === 'mock' ? '跑确定性回测中…' : 'LLM 决策回测中(较慢)…'} />}
      {run.error && <ErrorBox error={run.error} />}

      {d && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {(d.warnings || []).length > 0 && <div className="mono" style={{ fontSize: 10, color: 'var(--jin)' }}>⚠ {d.warnings.join(' · ')}</div>}
          {_isShort(d) && (
            <div style={{
              padding: '10px 14px',
              border: '1px solid var(--jin)', background: '#fff5e6',
              fontSize: 11.5, color: 'var(--ink-1)',
            }}>
              <strong>⚠ 样本 {d.nav.dates.length} 个交易日 &lt; 30, KPI 仅参考</strong> ·
              年化 / Sharpe / Calmar / 波动率 都对短窗口做了 √250 或 ^(250/N) 放大, 噪声会被放大数十倍.
              要看真实策略表现请跑 60+ 交易日窗口.
            </div>
          )}
          <BacktestSummaryChips d={d} onPoolClick={() => setShowPoolPopover(true)} />
          <Panel title={<span>组合表现 <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 6 }}>{d.mode}·{d.params && d.params.start}~{d.params && d.params.end}·LLM {k.n_llm_calls} 次</span></span>}>
            {/* 8 格, 对齐 FactorReportView 的组合回测格 */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', border: '1px solid var(--line-soft)' }}>
              {_isShort(d) ? (
                <Kpi label={<span>区间收益<span style={{ color: 'var(--jin)', marginLeft: 3 }}>⚠</span></span>}
                     value={pct(_intervalRet(d.nav.series))}
                     dir={dirOf(_intervalRet(d.nav.series))}
                     tooltip={`样本 ${d.nav.dates.length} 日 < 30, 显示区间总收益替代年化 (年化放大失真). 公式: (末值 − 首值) / 首值`} />
              ) : (
                <Kpi label="年化"     value={pct(k.ann_return)} dir={dirOf(k.ann_return)}
                     tooltip="年化收益率 = (1 + 区间总收益)^(250/区间交易日) − 1" />
              )}
              <Kpi label={<>Sharpe{_isShort(d) && <span style={{ color: 'var(--jin)', marginLeft: 3, fontSize: 10 }}>⚠</span>}</>}
                   value={n2(k.sharpe, 2)}
                   tooltip="夏普比率 = 年化收益 / 年化波动率 (无风险=0)" />
              <Kpi label="最大回撤"  value={pct(k.max_drawdown)} dir={k.max_drawdown ? 'down' : undefined}
                   tooltip="最大回撤 = max((peak − trough) / peak), 滚动统计" />
              <Kpi label={<>Calmar{_isShort(d) && <span style={{ color: 'var(--jin)', marginLeft: 3, fontSize: 10 }}>⚠</span>}</>}
                   value={n2(k.calmar, 2)} last
                   tooltip="年化收益 / |最大回撤| · 抗回撤能力指标" />
              <Kpi label={<>波动率{_isShort(d) && <span style={{ color: 'var(--jin)', marginLeft: 3, fontSize: 10 }}>⚠</span>}</>}
                   value={pct(k.volatility)}
                   tooltip="年化波动率 = std(日收益) × √250" />
              <Kpi label="换手"     value={pct(k.turnover)}
                   tooltip="区间总成交额 / 期末总资产 / 年化系数 (来自 portfolio.py)" />
              <Kpi label="胜率(日)"  value={pct(k.win_rate)}
                   tooltip="净值正收益日数 / 总交易日数" />
              {(() => {
                const nSells = (d.trades || []).filter(t => t.action === 'sell').length;
                if (nSells === 0) {
                  return <Kpi label="逐笔胜率" value="—" last
                              tooltip="窗口内无 sell 完成 (LLM 一直 hold 或 mock buy 后没等到 hold_days), 无法计算逐笔胜率" />;
                }
                return <Kpi label="逐笔胜率" value={pct(k.trade_win_rate)} last
                            tooltip={`${nSells} 笔 sell · 盈利卖单 / 总卖单 (action='sell' 且 pnl > 0)`} />;
              })()}
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
                  <div key={i} className="hover-row"
                    onClick={() => setSelectedTrade(t)}
                    style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '7px 10px', borderBottom: '1px solid var(--line-soft)', cursor: 'pointer' }}>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', width: 78 }}>{t.date}</span>
                    <span className="mono" style={{ fontSize: 10, width: 46, color: t.action === 'buy' ? 'var(--zhu)' : 'var(--dai)' }}>{t.action}</span>
                    <code className="mono" style={{ fontSize: 11.5, color: 'var(--ink)', width: 84 }}>{t.code}</code>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--ink-1)', width: 72 }}>{n2(t.price, 2)}</span>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', width: 64 }}>{t.qty}</span>
                    <span className={'mono ' + (t.pnl > 0 ? 'up' : t.pnl < 0 ? 'down' : '')} style={{ fontSize: 11, width: 88 }}>{t.action === 'sell' ? n2(t.pnl, 1) : '—'}</span>
                    <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{reasonFor(t)} <span style={{ color: 'var(--ink-3)', fontSize: 9 }}>🔍</span></span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      )}
      {!d && !run.loading && !polling && !run.error && <Empty label="设定窗口 + Mock 模式, 点「起回测」秒级出净值 + 交易 (Mock=单只反转买入持有 N 日后了结, 演示数据通路, 非盈利策略)" />}
      {showPoolPopover && d && (
        <PoolFilterPopover
          pool={(d.params && d.params.pool) || 'csi300'}
          codes={(d.params && d.params.codes) || null}
          topn={(d.params && d.params.candidate_topn) || 20}
          stats={d.candidate_filter_stats}
          onClose={() => setShowPoolPopover(false)} />
      )}
      {selectedTrade && <TradeReasonModal trade={selectedTrade} d={d} onClose={() => setSelectedTrade(null)} />}
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
// onOpen(item) → 打开 RecDetailModal 看 LLM 全文 (P0.3)。
function RecCard({ item, onAck, onOpen }) {
  const { code, ts, rec, ack } = item;
  const r = rec || {};
  const dir = ACTION_DIR[r.action] || '';
  return (
    <div onClick={() => onOpen && onOpen(item)} className="hover-row"
      style={{ border: '1px solid var(--line)', background: 'var(--paper)', marginBottom: 10, cursor: 'pointer' }}>
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
          <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 'auto' }}>🔍</span>
        </div>
      </div>
      <div style={{ borderTop: '1px solid var(--line-soft)', padding: '6px 9px', display: 'flex', gap: 8 }}>
        {ack ? (
          <span className="mono" style={{ fontSize: 11, color: ack === 'confirm' ? 'var(--zhu)' : 'var(--ink-3)' }}>
            {ack === 'confirm' ? '✓ 已确认' : '— 已忽略'}
          </span>
        ) : (
          <>
            <button onClick={(e) => { e.stopPropagation(); onAck(item, 'confirm'); }} className="hover-pill"
              style={{ flex: 1, padding: '5px 8px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer' }}>确认</button>
            <button onClick={(e) => { e.stopPropagation(); onAck(item, 'ignore'); }} className="hover-pill"
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

// ─────── trigger_kind 元数据 (P1.2 静态 dict, 不开后端 endpoint) ───────
const TRIGGER_META = {
  price_drop_30m: { name: 'price_drop_30m', desc: '价格 30 分钟内跌幅超阈值', default_threshold: '-2%' },
  price_jump_30m: { name: 'price_jump_30m', desc: '价格 30 分钟内涨幅超阈值', default_threshold: '+2%' },
  break_high_20d: { name: 'break_high_20d', desc: '突破 20 日新高', default_threshold: '收盘 > 20 日最高价' },
  break_low_20d:  { name: 'break_low_20d',  desc: '跌破 20 日新低', default_threshold: '收盘 < 20 日最低价' },
  stop_break:     { name: 'stop_break',     desc: '价格跌破用户设定 stop_loss 位', default_threshold: '用户每只独立设置' },
  volume_surge:   { name: 'volume_surge',   desc: '成交量异常放大', default_threshold: '当日成交量 > 5日均量 × 3' },
  news_event:     { name: 'news_event',     desc: '新闻事件命中关键词', default_threshold: '由后端 signals.py 关键词表决定' },
};

// ─────── WatchStrategyBanner — 顶部 banner 说清盯盘工作流 (P0.1) ───────
function WatchStrategyBanner({ running }) {
  if (!running) {
    return (
      <div style={{
        padding: '12px 16px', marginBottom: 12,
        border: '1px solid var(--line)', background: 'var(--paper-1)',
        borderLeft: '3px solid var(--ink-3)', fontSize: 12,
      }}>
        <div className="serif" style={{ fontSize: 13, color: 'var(--ink)', marginBottom: 6 }}>
          ⚪ <strong>盯盘未开始</strong> — 左侧自选列表添加股票, 按 ▶ 开始盯盘
        </div>
        <div style={{ color: 'var(--ink-2)', lineHeight: 1.7 }}>
          工作流: 每 60s tick 拉行情 → 事件触发 → LLM 出 5 档建议 → 你确认
        </div>
      </div>
    );
  }
  return (
    <div style={{
      padding: '12px 16px', marginBottom: 12,
      border: '1px solid var(--line)', background: 'var(--paper-1)',
      borderLeft: '3px solid var(--zhu)', fontSize: 12,
    }}>
      <div className="serif" style={{ fontSize: 13, color: 'var(--ink)', marginBottom: 6 }}>
        🔴 <strong>实时盯盘运行中</strong> — Tencent realtime + 事件触发 + LLM 决策
      </div>
      <div style={{ color: 'var(--ink-2)', lineHeight: 1.7 }}>
        每 60s tick: 拉自选最新行情 → 跑事件触发器 → 触发即调 qwen3.5-plus 出 5 档建议
        (buy/add/hold/reduce/sell) → SSE 推到右侧 confirm/ignore.
      </div>
      <div style={{ color: 'var(--ink-3)', fontSize: 11, marginTop: 6 }}>
        防刷: 同 (code, trigger_kind) 冷却 15 min · 全 session 最多 20 次 LLM 调用
      </div>
    </div>
  );
}

// ─────── WatchStatusChips — 一行 chip 串状态横条 (P0.2) ───────
function WatchStatusChips({ status, recs }) {
  const s = status || {};
  const nItems = s.n_items || 0;
  const tickCount = s.tick_count || 0;
  const tickSeconds = s.tick_seconds || 60;
  const cooldown = s.cooldown_minutes || 15;
  const cap = s.global_llm_cap_per_session || 20;
  const made = s.llm_calls_made || 0;
  const remaining = Math.max(0, cap - made);
  const connTxt = s.conn === 'open' ? '●已连' : s.conn === 'error' ? '●断' : '○未连';
  const connColor = s.conn === 'open' ? 'var(--zhu)' : s.conn === 'error' ? 'var(--dai)' : 'var(--ink-3)';
  const ackConfirm = (recs || []).filter(r => r.ack === 'confirm').length;
  const ackIgnore = (recs || []).filter(r => r.ack === 'ignore').length;
  const ackPending = (recs || []).filter(r => !r.ack).length;
  return (
    <div style={{
      padding: '10px 14px', marginBottom: 14,
      border: '1px solid var(--line-soft)', background: 'var(--paper-2)',
      display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12,
      fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--ink-2)',
    }}>
      <span>自选 <strong>{nItems}</strong> 只</span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span>tick <strong>{tickCount}</strong></span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: connColor }}>SSE {connTxt}</span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span>tick <strong>{tickSeconds}s</strong></span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span>冷却 <strong>{cooldown}min</strong></span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span>LLM <strong>{made}/{cap}</strong> 次 (剩 <strong>{remaining}</strong> 次)</span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span>推荐 <strong>{(recs || []).length}</strong> 条</span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span>已确认 <strong>{ackConfirm}</strong> / 忽略 <strong>{ackIgnore}</strong> / 待处理 <strong>{ackPending}</strong></span>
    </div>
  );
}

// ─────── RecDetailModal — 推荐点开看 LLM 全文 (P0.3) ───────
function RecDetailModal({ rec, onClose }) {
  const [rawExpanded, setRawExpanded] = useState(false);
  if (!rec) return null;
  const r = rec.rec || {};
  const trigKind = r.trigger_kind || '';
  const meta = TRIGGER_META[trigKind];
  const dir = ACTION_DIR[r.action] || '';
  const raw = r.raw || null;
  const rawStr = raw ? JSON.stringify(raw, null, 2) : null;
  const rawPreview = rawStr && rawStr.length > 200 ? rawStr.slice(0, 200) + '…' : rawStr;
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(20,20,20,0.5)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        maxWidth: 680, width: '100%', maxHeight: '85vh', overflow: 'auto',
        padding: 22, background: 'var(--paper)', border: '1px solid var(--line)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14 }}>
          <span className="serif" style={{ fontSize: 14 }}>
            {rec.ts || ''} · <code className="mono">{rec.code}</code> · <span className={'mono ' + dir} style={{ fontWeight: 600 }}>
              {ACTION_CN[r.action] || r.action || '—'}
            </span>
          </span>
          <button onClick={onClose} style={{
            border: 'none', background: 'transparent', fontSize: 18, cursor: 'pointer', color: 'var(--ink-3)',
          }}>×</button>
        </div>

        <BacktestModalSection title="触发原因 (trigger_kind)">
          {meta ? (
            <div className="mono" style={{ fontSize: 11.5, lineHeight: 1.8, color: 'var(--ink-2)' }}>
              <div><code style={{ color: 'var(--ink)' }}>{meta.name}</code>: {meta.desc}</div>
              <div>阈值: {meta.default_threshold}</div>
            </div>
          ) : (
            <div className="mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
              {trigKind ? `${trigKind} (未识别 trigger_kind, 见后端 signals.py)` : '—'}
            </div>
          )}
        </BacktestModalSection>

        <BacktestModalSection title="LLM 决策">
          <div className="mono" style={{ fontSize: 11.5, lineHeight: 1.8, color: 'var(--ink-2)' }}>
            <div>
              <span className={'mono ' + dir} style={{ fontWeight: 600 }}>{ACTION_CN[r.action] || r.action || '—'}</span>
              {r.weight_pct ? <span> · 权重 {r.weight_pct}%</span> : null}
              {(r.confidence || r.confidence === 0) ? <span> · 信心 {pct(r.confidence, 0)}</span> : null}
            </div>
            <div>
              {r.target_price > 0 && <span>目标价 {n2(r.target_price, 2)}</span>}
              {r.target_price > 0 && r.stop_loss > 0 && <span> · </span>}
              {r.stop_loss > 0 && <span>止损 {n2(r.stop_loss, 2)}</span>}
              {!(r.target_price > 0) && !(r.stop_loss > 0) && <span style={{ color: 'var(--ink-3)' }}>无 target/stop</span>}
            </div>
          </div>
        </BacktestModalSection>

        <BacktestModalSection title="LLM reason">
          <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.7 }}>
            {r.reason || (r.error ? '✗ ' + r.error : '—')}
          </div>
        </BacktestModalSection>

        {rawStr && (
          <BacktestModalSection title="LLM 返回原文 (raw JSON)">
            <pre className="mono" style={{
              fontSize: 10.5, padding: 10, background: 'var(--paper-2)', border: '1px solid var(--line-soft)',
              whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
            }}>{rawExpanded ? rawStr : rawPreview}</pre>
            {rawStr.length > 200 && (
              <button onClick={() => setRawExpanded(!rawExpanded)} style={{
                marginTop: 6, border: 'none', background: 'transparent', color: 'var(--zhu)',
                fontSize: 10.5, cursor: 'pointer', fontFamily: 'var(--mono)',
              }}>{rawExpanded ? '收起 ▴' : '展开看全文 ▾'}</button>
            )}
          </BacktestModalSection>
        )}
      </div>
    </div>
  );
}

// ─────── TriggerPopover — ⚡ 点击看 trigger_kind 含义 (P1.2) ───────
function TriggerPopover({ code, kind, ts, count, onClose }) {
  const meta = TRIGGER_META[kind];
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(20,20,20,0.3)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: 360, padding: 18, background: 'var(--paper)', border: '1px solid var(--ink)',
        boxShadow: '3px 3px 0 -1px var(--paper-3)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
          <span className="serif" style={{ fontSize: 13, color: 'var(--ink)' }}>
            上次触发 {ts ? `(${String(ts).slice(-8, -3) || String(ts).slice(0, 10)})` : ''}
          </span>
          <button onClick={onClose} style={{
            border: 'none', background: 'transparent', fontSize: 16, cursor: 'pointer', color: 'var(--ink-3)',
          }}>×</button>
        </div>
        <div className="mono" style={{ fontSize: 11.5, lineHeight: 1.9, color: 'var(--ink-2)' }}>
          <div><span style={{ color: 'var(--ink-3)' }}>code:</span> <code style={{ color: 'var(--ink)' }}>{code || '—'}</code></div>
          <div><span style={{ color: 'var(--ink-3)' }}>trigger_kind:</span> <code style={{ color: 'var(--ink)' }}>{kind || '—'}</code></div>
          {meta ? (
            <>
              <div><span style={{ color: 'var(--ink-3)' }}>含义:</span> {meta.desc}</div>
              <div><span style={{ color: 'var(--ink-3)' }}>默认阈值:</span> {meta.default_threshold}</div>
            </>
          ) : (
            <div style={{ color: 'var(--ink-3)' }}>未识别 trigger_kind, 见后端 signals.py</div>
          )}
          {count !== undefined && count !== null && (
            <div><span style={{ color: 'var(--ink-3)' }}>已触发:</span> {count} 次 (本 session)</div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─────── WatchAdvancedControls — 高级控件折叠区 (P2.1) ───────
function WatchAdvancedControls({ tickSeconds, setTickSeconds, cooldown, setCooldown, llmCap, setLlmCap, running }) {
  const disabledTitle = running ? '运行中无法热改 — 请停止后再修改' : '';
  return (
    <div style={{
      display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap',
      padding: '10px 12px', marginBottom: 12, border: '1px solid var(--line-soft)', background: 'var(--paper-1)',
    }}>
      <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }} title={disabledTitle}>
        tick 间隔 (秒)
        <input type="number" min={1} max={600} value={tickSeconds} disabled={running}
          onChange={e => setTickSeconds(Number(e.target.value) || 60)}
          style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: 80,
                   border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12,
                   opacity: running ? 0.4 : 1 }} />
      </label>
      <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }} title={disabledTitle}>
        冷却 (min)
        <input type="number" min={1} max={120} value={cooldown} disabled={running}
          onChange={e => setCooldown(Number(e.target.value) || 15)}
          style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: 80,
                   border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12,
                   opacity: running ? 0.4 : 1 }} />
      </label>
      <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }} title={disabledTitle}>
        LLM cap (次/session)
        <input type="number" min={1} max={200} value={llmCap} disabled={running}
          onChange={e => setLlmCap(Number(e.target.value) || 20)}
          style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: 80,
                   border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12,
                   opacity: running ? 0.4 : 1 }} />
      </label>
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
        注: 修改后下次 ▶ 开始盯盘 生效 (运行中无法热改)
      </span>
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
  const [firedTs, setFiredTs] = useState({});      // {code: lastFireTs}
  const [firedCount, setFiredCount] = useState({}); // {code: count}
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
  // P0/P1/P2: 新增 UI state (banner/横条/modal/popover/高级控件)
  const [showAdv, setShowAdv] = useState(false);
  const [tickSeconds, setTickSeconds] = useState(60);
  const [cooldown, setCooldown] = useState(15);
  const [llmCap, setLlmCap] = useState(20);
  const [llmCallsMade, setLlmCallsMade] = useState(0);
  const [selectedRec, setSelectedRec] = useState(null);
  const [selectedTrigger, setSelectedTrigger] = useState(null);
  // P1.1: 自选 chip 编辑 (avg_cost / stop_loss)
  const [editingCode, setEditingCode] = useState(null);    // 当前打开编辑 popover 的 code
  const [editAvgCost, setEditAvgCost] = useState('');
  const [editStopLoss, setEditStopLoss] = useState('');

  // 初始 status (P0.2 同步当前 cfg)
  useEffect(() => {
    getJSON('/watch/status').then(s => {
      if (!s) return;
      setRunning(!!s.running);
      setItems(s.items || []);
      setTickCount(s.tick_count || 0);
      setLlmCallsMade(s.llm_calls_made || 0);
      if (s.tick_seconds) setTickSeconds(Number(s.tick_seconds) || 60);
      if (s.cooldown_minutes) setCooldown(Number(s.cooldown_minutes) || 15);
      if (s.global_llm_cap_per_session) setLlmCap(Number(s.global_llm_cap_per_session) || 20);
      if (!sel && (s.items || []).length) setSel(s.items[0].code);
    }).catch(() => {});
  }, []);

  // running 翻 true 后再拉一次 status (拿到 loop 实际生效的 cfg + llm_calls_made)
  useEffect(() => {
    if (!running) return;
    getJSON('/watch/status').then(s => {
      if (!s) return;
      setTickCount(s.tick_count || 0);
      setLlmCallsMade(s.llm_calls_made || 0);
      if (s.tick_seconds) setTickSeconds(Number(s.tick_seconds) || 60);
      if (s.cooldown_minutes) setCooldown(Number(s.cooldown_minutes) || 15);
      if (s.global_llm_cap_per_session) setLlmCap(Number(s.global_llm_cap_per_session) || 20);
    }).catch(() => {});
  }, [running]);

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
      const kind = (d.rec && d.rec.trigger_kind) || 'signal';
      setRecs(prev => [{ key, code: d.code, ts: d.ts, rec: d.rec || {}, ack: null }, ...prev].slice(0, 100));
      setFired(prev => ({ ...prev, [d.code]: kind }));
      setFiredTs(prev => ({ ...prev, [d.code]: d.ts || '' }));
      setFiredCount(prev => ({ ...prev, [d.code]: (prev[d.code] || 0) + 1 }));
      // 用 LLM 一次 → 本地 +1 (后端 /watch/status 也会改, 但浏览器无 polling)
      setLlmCallsMade(n => n + 1);
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
    // 已添加的 items 保留 avg_cost/stop_loss (P1.1); 临时输入 draft 走 parseCodes 无字段
    const itemsBody = items.length
      ? items.map(it => {
          const out = { code: it.code };
          if (it.avg_cost !== undefined && it.avg_cost !== null && it.avg_cost !== '') out.avg_cost = Number(it.avg_cost);
          if (it.stop_loss !== undefined && it.stop_loss !== null && it.stop_loss !== '') out.stop_loss = Number(it.stop_loss);
          return out;
        })
      : parseCodes(draft).map(c => ({ code: c }));
    if (!itemsBody.length) { setErr('请先添加至少一只股票'); return; }
    try {
      const body = {
        items: itemsBody,
        // P2.2: 高级控件透传 (null 走后端默认)
        tick_seconds: Number(tickSeconds) || null,
        cooldown_minutes: Number(cooldown) || null,
        global_llm_cap_per_session: Number(llmCap) || null,
      };
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

  // P1.1: 打开 chip 编辑 popover, 把当前值灌到 local state
  const openEdit = (it) => {
    setEditingCode(it.code);
    setEditAvgCost(it.avg_cost !== undefined && it.avg_cost !== null ? String(it.avg_cost) : '');
    setEditStopLoss(it.stop_loss !== undefined && it.stop_loss !== null ? String(it.stop_loss) : '');
  };
  // P1.1: 保存 — 本地更新 items (running 时下次 ▶ 重启才生效, 简化版不调后端 remove+add)
  const saveEdit = () => {
    if (!editingCode) return;
    const ac = editAvgCost === '' ? null : Number(editAvgCost);
    const sl = editStopLoss === '' ? null : Number(editStopLoss);
    setItems(prev => prev.map(it => it.code === editingCode ? { ...it, avg_cost: ac, stop_loss: sl } : it));
    setEditingCode(null);
    setEditAvgCost(''); setEditStopLoss('');
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
        <button onClick={() => setShowAdv(!showAdv)} className="hover-pill"
          style={{ padding: '5px 11px', border: '1px solid var(--line)', background: 'transparent',
                   cursor: 'pointer', fontSize: 11.5, fontFamily: 'var(--serif)' }}>
          高级 {showAdv ? '▴' : '▾'}
        </button>
      </div>
      {err && <div style={{ padding: '8px 16px' }}><ErrorBox error={err} /></div>}

      {/* P0.1 banner + P2.1 高级控件折叠区 + P0.2 状态横条 (仅在 live 视图显示) */}
      {view === 'live' && (
        <div style={{ padding: '12px 16px 0' }}>
          <WatchStrategyBanner running={running} />
          {showAdv && (
            <WatchAdvancedControls
              tickSeconds={tickSeconds} setTickSeconds={setTickSeconds}
              cooldown={cooldown} setCooldown={setCooldown}
              llmCap={llmCap} setLlmCap={setLlmCap}
              running={running} />
          )}
          {(running || tickCount > 0 || recs.length > 0) && (
            <WatchStatusChips
              status={{
                n_items: items.length,
                tick_count: tickCount,
                tick_seconds: tickSeconds,
                cooldown_minutes: cooldown,
                global_llm_cap_per_session: llmCap,
                llm_calls_made: llmCallsMade,
                conn,
              }}
              recs={recs} />
          )}
        </div>
      )}

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
            const cost = (it.avg_cost !== undefined && it.avg_cost !== null) ? Number(it.avg_cost) : null;
            const stop = (it.stop_loss !== undefined && it.stop_loss !== null) ? Number(it.stop_loss) : null;
            const floatPct = (cost && px && cost > 0) ? (px - cost) / cost : null;
            const floatDir = (floatPct !== null) ? (floatPct > 0 ? 'up' : floatPct < 0 ? 'down' : '') : '';
            return (
              <div key={code} className="hover-row" onClick={() => setSel(code)}
                style={{ padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid var(--line-soft)', background: sel === code ? 'rgba(28,24,20,0.07)' : 'transparent' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <code className="mono" style={{ fontSize: 12, color: 'var(--ink)' }}>{code}</code>
                      {fired[code] && (
                        <span onClick={(e) => {
                          e.stopPropagation();
                          setSelectedTrigger({ code, kind: fired[code], ts: firedTs[code], count: firedCount[code] });
                        }}
                          title="点击看 trigger 详情" style={{ fontSize: 11, color: 'var(--jin)', cursor: 'pointer' }}>⚡</span>
                      )}
                    </div>
                    <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{fired[code] || ''}</div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div className={'mono ' + dir} style={{ fontSize: 12 }}>{n2(px, 2)}</div>
                    <div className={'mono ' + dir} style={{ fontSize: 10 }}>{(chg === undefined || chg === null) ? '—' : (chg > 0 ? '+' : '') + n2(chg, 2) + '%'}</div>
                  </div>
                  <span onClick={(e) => { e.stopPropagation(); openEdit(it); }} title="编辑成本/止损"
                    style={{ cursor: 'pointer', color: 'var(--ink-3)', opacity: 1, fontSize: 13, lineHeight: 1, padding: '0 2px' }}>⋯</span>
                  <span onClick={(e) => { e.stopPropagation(); removeCode(code); }} title="移除"
                    style={{ cursor: 'pointer', color: 'var(--yin)', opacity: 1, fontWeight: 600, fontSize: 13, lineHeight: 1 }}>×</span>
                </div>
                {/* P1.1: 成本/止损/浮动收益 (avg_cost 已填才显示一行) */}
                {(cost !== null || stop !== null) && (
                  <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 4, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    {cost !== null && <span>成本 {n2(cost, 2)}</span>}
                    {floatPct !== null && <span className={floatDir}>浮动 {(floatPct > 0 ? '+' : '') + n2(floatPct * 100, 2)}%</span>}
                    {stop !== null && <span>止损 {n2(stop, 2)}</span>}
                  </div>
                )}
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
                <Kpi label="现价" value={n2(quotes[sel].price !== undefined ? quotes[sel].price : quotes[sel].now, 2)}
                  tooltip="Tencent realtime 推送的最新成交价 (盘中) / 上一交易日收盘 (盘后)" />
                <Kpi label="涨跌%" value={n2(quotes[sel].changePercent, 2)} dir={(quotes[sel].changePercent > 0) ? 'up' : (quotes[sel].changePercent < 0) ? 'down' : ''}
                  tooltip="(price - prev_close) / prev_close · prev_close 来自 Tencent realtime" />
                <Kpi label="最高" value={n2(quotes[sel].high, 2)}
                  tooltip="当日最高价 (Tencent realtime 累计)" />
                <Kpi label="最低" value={n2(quotes[sel].low, 2)} last
                  tooltip="当日最低价 (Tencent realtime 累计)" />
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
          {recs.map(item => <RecCard key={item.key} item={item} onAck={ack} onOpen={setSelectedRec} />)}
        </aside>
      </div>
      )}

      {view === 'review' && (
        <WatchReview hitrate={hitrate} rows={histRows} busy={reviewBusy}
                     err={reviewErr} onBackfill={backfill} onReload={loadReview} />
      )}

      {/* P0.3 推荐详情 modal */}
      {selectedRec && <RecDetailModal rec={selectedRec} onClose={() => setSelectedRec(null)} />}

      {/* P1.2 ⚡ trigger popover */}
      {selectedTrigger && <TriggerPopover {...selectedTrigger} onClose={() => setSelectedTrigger(null)} />}

      {/* P1.1 chip 编辑 popover (avg_cost/stop_loss) */}
      {editingCode && (
        <div onClick={() => setEditingCode(null)} style={{
          position: 'fixed', inset: 0, background: 'rgba(20,20,20,0.3)', zIndex: 100,
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            width: 360, padding: 18, background: 'var(--paper)', border: '1px solid var(--ink)',
            boxShadow: '3px 3px 0 -1px var(--paper-3)',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
              <span className="serif" style={{ fontSize: 13, color: 'var(--ink)' }}>编辑 <code className="mono">{editingCode}</code></span>
              <button onClick={() => setEditingCode(null)} style={{
                border: 'none', background: 'transparent', fontSize: 16, cursor: 'pointer', color: 'var(--ink-3)',
              }}>×</button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                avg_cost (持仓成本)
                <input type="number" step="0.01" value={editAvgCost} onChange={e => setEditAvgCost(e.target.value)}
                  placeholder="留空清除"
                  style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: '100%', boxSizing: 'border-box',
                           border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12 }} />
              </label>
              <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                stop_loss (止损位)
                <input type="number" step="0.01" value={editStopLoss} onChange={e => setEditStopLoss(e.target.value)}
                  placeholder="留空清除"
                  style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: '100%', boxSizing: 'border-box',
                           border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12 }} />
              </label>
              <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                <button onClick={saveEdit} className="hover-pill"
                  style={{ flex: 1, padding: '6px 10px', border: 'none', background: 'var(--ink)', color: 'var(--paper)',
                           fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer' }}>保存</button>
                <button onClick={() => setEditingCode(null)} className="hover-pill"
                  style={{ flex: 1, padding: '6px 10px', border: '1px solid var(--line)', background: 'transparent',
                           color: 'var(--ink-2)', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer' }}>取消</button>
              </div>
              {running && (
                <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>
                  注: 运行中只在本地记录, 下次 ▶ 开始盯盘 重启时透传到后端
                </div>
              )}
            </div>
          </div>
        </div>
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
        {mode === 'workflow' && <WorkflowLab />}
        {mode === 'backtest' && <BacktestMode />}   {/* ← P5 新增 */}
        {mode === 'watch' && <WatchMode />}
      </div>
    </div>
  );
}

window.QuantApp = QuantApp;
