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
function LibraryMode() { return <Empty label="因子库 & 详情 (待接 C.2)" />; }
function ForgeMode() { return <Empty label="炼因子 (待接 C.3)" />; }
function ComposeMode() { return <Empty label="多因子合成 (待接 C.4a)" />; }
function ArchiveMode() { return <Empty label="研究档案 (待接 C.4b)" />; }

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
