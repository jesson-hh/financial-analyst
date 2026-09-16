/* 观澜 · 板块资金流向 — 纯展示层(绝不混入交易信号)。红涨绿跌 A股口径。 */
const { useState, useEffect, useCallback } = React;

const YI = 1e8;
const fmtYi = (v) => (v == null || !Number.isFinite(Number(v)) ? "—" : `${(Number(v) / YI).toFixed(2)}亿`);
const signedYi = (v) => (v == null || !Number.isFinite(Number(v)) ? "—" : `${Number(v) >= 0 ? "+" : ""}${fmtYi(v)}`);
const flowTone = (v) => (v == null ? "muted" : Number(v) >= 0 ? "up" : "down");
const lastValue = (series) => (series || []).reduce((acc, v) => (v != null ? Number(v) : acc), null);

function SectionHeading({ eyebrow, title, detail, aside }) {
  return (
    <div className="ff-section-heading">
      <div>
        <div className="ff-section-eyebrow">{eyebrow}</div>
        <div className="ff-section-title-row">
          <h2>{title}</h2>
          {detail && <span>{detail}</span>}
        </div>
      </div>
      {aside && <div className="ff-section-aside">{aside}</div>}
    </div>
  );
}

function BreadthMetric({ label, data }) {
  const up = data && Number.isFinite(Number(data.up)) ? Number(data.up) : null;
  const down = data && Number.isFinite(Number(data.down)) ? Number(data.down) : null;
  const total = up == null || down == null ? 0 : up + down;
  const upPct = total > 0 ? (up / total) * 100 : 0;
  const dominant = total === 0 ? "等待独立源" : up >= down ? "上涨占优" : "下跌占优";
  return (
    <article className={`ff-metric-card ${total === 0 ? "is-muted" : ""}`}>
      <div className="ff-metric-label">{label}<span>{dominant}</span></div>
      <div className="ff-breadth-numbers">
        <strong className="is-up">{up == null ? "—" : up}</strong>
        <i>/</i>
        <strong className="is-down">{down == null ? "—" : down}</strong>
      </div>
      <div className="ff-breadth-caption"><span>上涨</span><span>下跌</span></div>
      <div className="ff-ratio-track" aria-label={`${label}上涨比例`}>
        <span className="is-up" style={{ width: `${upPct}%` }} />
        <span className="is-down" style={{ width: `${100 - upPct}%` }} />
      </div>
    </article>
  );
}

function OverviewGrid({ live, fromMemory }) {
  const market = (live && live.market) || {};
  const breadth = (live && live.breadth) || {};
  const main = market.main_net;
  const delayed = String(market.src_host || "").includes("delay");
  const archived = !live || !live.trading;
  return (
    <section className="ff-overview-grid" aria-label="资金流摘要">
      <article className={`ff-metric-card ff-main-card is-${flowTone(main)}`}>
        <div className="ff-metric-label">大盘主力<span>{main == null ? "数据不可用" : Number(main) >= 0 ? "净流入" : "净流出"}</span></div>
        <div className={`ff-main-value is-${flowTone(main)}`}>{signedYi(main)}</div>
        <div className="ff-main-helper">超大单与大单合计 · 沪深市场</div>
      </article>
      <BreadthMetric label="行业广度" data={breadth.industry} />
      <BreadthMetric label="概念广度" data={breadth.concept} />
      <article className="ff-metric-card ff-status-card">
        <div className="ff-metric-label">数据状态<span className={delayed ? "is-caution" : "is-ready"}>{delayed ? "延时源" : "主源"}</span></div>
        <div className="ff-status-value"><span className={archived ? "is-archive" : "is-live"} />{archived ? "盘后存档" : "盘中更新"}</div>
        <div className="ff-main-helper">{fromMemory ? "浏览器本地记忆" : "服务端最新快照"}</div>
      </article>
    </section>
  );
}

function MarketFlowBars({ m }) {
  const items = [
    ["超大单", "机构级大额成交", m && m.super_net],
    ["大单", "主动性大额成交", m && m.large_net],
    ["中单", "中等规模成交", m && m.mid_net],
    ["小单", "个人投资者成交", m && m.small_net],
    ["主力", "超大单 + 大单", m && m.main_net],
  ];
  const max = Math.max(1, ...items.map(([, , v]) => Math.abs(Number(v) || 0)));
  return (
    <section className="ff-panel ff-market-panel">
      <SectionHeading
        eyebrow="MARKET STRUCTURE"
        title="大盘资金分层"
        detail={m && m.date ? `${m.date} · 沪深合计` : "源不可用，不编造"}
        aside={m && m.src_host && String(m.src_host).includes("delay") ? <span className="ff-source-pill">延时源</span> : null}
      />
      <div className="ff-flow-list">
        {items.map(([label, desc, v]) => {
          const width = `${(Math.abs(Number(v) || 0) / max) * 50}%`;
          return (
            <div className={`ff-flow-row ${label === "主力" ? "is-primary" : ""}`} key={label}>
              <div className="ff-flow-name"><strong>{label}</strong><span>{desc}</span></div>
              <div className="ff-flow-track">
                <span className="ff-flow-zero" />
                <span className={`ff-flow-fill is-${flowTone(v)}`} style={{ width, left: (Number(v) || 0) >= 0 ? "50%" : "auto", right: (Number(v) || 0) < 0 ? "50%" : "auto" }} />
              </div>
              <strong className={`ff-flow-value is-${flowTone(v)}`}>{signedYi(v)}</strong>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function niceStep(maxAbsYi) {
  const raw = maxAbsYi / 2;
  const pow = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1))));
  const n = raw / pow;
  const mult = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 3 ? 3 : n <= 4 ? 4 : n <= 5 ? 5 : 10;
  return mult * pow;
}

function focusSeries(boards) {
  const rows = (boards || []).map((b) => ({ ...b, _last: lastValue(b.series) })).filter((b) => b._last != null);
  const inflow = rows.filter((b) => b._last >= 0).sort((a, b) => b._last - a._last).slice(0, 4);
  const outflow = rows.filter((b) => b._last < 0).sort((a, b) => a._last - b._last).slice(0, 4);
  const red = ["#8F2D22", "#B23A2B", "#C75A47", "#D77C68"];
  const green = ["#294B3D", "#3E6152", "#58796A", "#789286"];
  return inflow.map((b, i) => ({ ...b, _color: red[i] })).concat(outflow.map((b, i) => ({ ...b, _color: green[i] })));
}

function IntradayChart({ hist }) {
  const ticks = (hist && hist.ticks) || [];
  const boards = focusSeries((hist && hist.boards) || []);
  const mkt = ((hist && hist.market_series) || {}).main_net || [];
  if (ticks.length < 2 || !boards.length) {
    return (
      <section className="ff-panel ff-chart-empty">
        <span className={hist && hist.warming ? "ff-spinner" : "ff-empty-mark"}>{hist && hist.warming ? "" : "∿"}</span>
        <strong>{hist && hist.warming ? "正在构建分钟资金轨迹" : "暂无当日分钟线"}</strong>
        <p>{hist && hist.warming ? "首次拉取约需 25 秒，完成后自动出图。" : "非交易日、盘前或上游源降级时不会合成虚假曲线。"}</p>
      </section>
    );
  }

  const W = 920, H = 380, PL = 58, PR = 18, PT = 18, PB = 34;
  const all = boards.flatMap((b) => b.series).concat(mkt).filter((v) => v != null);
  const maxAbs = Math.max(1, ...all.map((v) => Math.abs(Number(v))));
  const stepYi = niceStep(maxAbs / YI);
  const spanYi = Math.max(stepYi, Math.ceil((maxAbs / YI) / stepYi) * stepYi);
  const x = (i) => PL + (i * (W - PL - PR)) / (ticks.length - 1);
  const y = (v) => PT + (H - PT - PB) * (0.5 - (Number(v) / (spanYi * YI)) * 0.5);
  const segments = (series) => {
    const parts = []; let current = [];
    (series || []).forEach((v, i) => {
      if (v == null) { if (current.length > 1) parts.push(current); current = []; }
      else current.push(`${x(i)},${y(v)}`);
    });
    if (current.length > 1) parts.push(current);
    return parts;
  };
  const gridYi = [];
  for (let v = -spanYi; v <= spanYi + 1e-9; v += stepYi) gridYi.push(Math.round(v * 100) / 100);
  const xIdx = [];
  const nx = Math.min(6, ticks.length);
  for (let i = 0; i < nx; i++) xIdx.push(Math.round((i * (ticks.length - 1)) / (nx - 1)));

  return (
    <section className="ff-panel ff-chart-panel">
      <SectionHeading
        eyebrow="INTRADAY TRACE"
        title="盘中资金轨迹"
        detail="仅展示流入、流出各前四名，减少曲线干扰"
        aside={hist && hist.date ? <span className="ff-date-tag">{hist.date}</span> : null}
      />
      <div className="ff-chart-layout">
        <div className="ff-chart-canvas">
          <svg className="ff-chart-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="板块盘中资金流向曲线">
            <defs>
              <linearGradient id="ffZeroGlow" x1="0" x2="1"><stop offset="0" stopColor="#B3A582" stopOpacity=".15" /><stop offset=".5" stopColor="#B3A582" stopOpacity=".55" /><stop offset="1" stopColor="#B3A582" stopOpacity=".15" /></linearGradient>
            </defs>
            {gridYi.map((g) => (
              <g key={g}>
                <line x1={PL} y1={y(g * YI)} x2={W - PR} y2={y(g * YI)} className={g === 0 ? "ff-grid-zero" : "ff-grid-line"} />
                <text x={PL - 9} y={y(g * YI) + 4} textAnchor="end" className="ff-axis-label">{g > 0 ? "+" : ""}{g}亿</text>
              </g>
            ))}
            {xIdx.map((i) => (
              <g key={i}>
                <line x1={x(i)} y1={PT} x2={x(i)} y2={H - PB} className="ff-grid-line is-vertical" />
                <text x={x(i)} y={H - 10} textAnchor="middle" className="ff-axis-label">{ticks[i]}</text>
              </g>
            ))}
            {mkt.some((v) => v != null) && segments(mkt).map((pts, pi) => (
              <polyline key={`market-${pi}`} points={pts.join(" ")} fill="none" stroke="var(--qing)" strokeWidth="2.2" strokeDasharray="7 5" opacity=".68" />
            ))}
            {boards.map((b) => (
              <g key={b.name}>{segments(b.series).map((pts, pi) => <polyline key={pi} points={pts.join(" ")} fill="none" stroke={b._color} strokeWidth="2.15" opacity=".9" />)}</g>
            ))}
          </svg>
        </div>
        <aside className="ff-chart-legend">
          <div className="ff-legend-group-label is-up">净流入领先</div>
          {boards.filter((b) => b._last >= 0).map((b, i) => (
            <div className="ff-legend-row" key={b.name}><i style={{ background: b._color }} /><span><b>{i + 1}</b>{b.name}</span><strong className="is-up">{signedYi(b._last)}</strong></div>
          ))}
          <div className="ff-legend-group-label is-down">净流出领先</div>
          {boards.filter((b) => b._last < 0).map((b, i) => (
            <div className="ff-legend-row" key={b.name}><i style={{ background: b._color }} /><span><b>{i + 1}</b>{b.name}</span><strong className="is-down">{signedYi(b._last)}</strong></div>
          ))}
          {mkt.some((v) => v != null) && <div className="ff-market-legend"><i />大盘主力 <strong>{signedYi(lastValue(mkt))}</strong></div>}
        </aside>
      </div>
    </section>
  );
}

function RankColumn({ title, subtitle, rows, tone }) {
  const max = Math.max(1, ...rows.map((b) => Math.abs(Number(b.main_net) || 0)));
  return (
    <div className="ff-rank-column">
      <div className={`ff-rank-column-title is-${tone}`}><div><span>{title}</span><small>{subtitle}</small></div><b>{rows.length}</b></div>
      <div className="ff-rank-rows">
        {rows.map((b, i) => (
          <div className="ff-rank-row" data-hv="row" key={b.code || b.name}>
            <span className="ff-rank-no">{String(i + 1).padStart(2, "0")}</span>
            <div className="ff-rank-name"><strong>{b.name}</strong><span className="ff-rank-bar"><i className={`is-${tone}`} style={{ width: `${(Math.abs(Number(b.main_net) || 0) / max) * 100}%` }} /></span></div>
            <span className={`ff-rank-pct is-${flowTone(b.change_pct)}`}>{Number(b.change_pct) >= 0 ? "+" : ""}{Number(b.change_pct || 0).toFixed(2)}%</span>
            <strong className={`ff-rank-value is-${tone}`}>{signedYi(b.main_net)}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function BoardRankTable({ boards }) {
  const rows = (boards || []).filter((b) => b && b.main_net != null);
  const inflow = rows.filter((b) => Number(b.main_net) >= 0).sort((a, b) => b.main_net - a.main_net).slice(0, 10);
  const outflow = rows.filter((b) => Number(b.main_net) < 0).sort((a, b) => a.main_net - b.main_net).slice(0, 10);
  return (
    <section className="ff-panel ff-ranking-panel">
      <SectionHeading eyebrow="CAPITAL RANKING" title="板块资金榜" detail="净流入与净流出双向对照 · 各前十" />
      <div className="ff-rank-grid">
        <RankColumn title="净流入" subtitle="资金正在聚集" rows={inflow} tone="up" />
        <RankColumn title="净流出" subtitle="资金正在撤离" rows={outflow} tone="down" />
      </div>
    </section>
  );
}

function App() {
  const [kind, setKind] = useState("concept");
  const [live, setLive] = useState(null);
  const [hist, setHist] = useState(null);
  const [loading, setLoading] = useState(true);
  const [fromMemory, setFromMemory] = useState(false);

  const update = useCallback(async (k) => {
    setLoading(true); setFromMemory(false);
    const [lv, hs] = await Promise.all([window.glFetchFundflowLive(k, true), window.glFetchFundflowHistory(k, "")]);
    setLive(lv); setHist(hs); setLoading(false);
    if (lv && lv.ok !== false && !(hs && hs.warming)) window.glSaveFundflowMemory(k, { live: lv, hist: hs });
  }, []);

  useEffect(() => {
    const mem = window.glLoadFundflowMemory(kind);
    if (mem && mem.live) {
      setLive(mem.live); setHist(mem.hist || null); setFromMemory(true); setLoading(false);
    } else update(kind);
  }, [kind, update]);

  useEffect(() => {
    if (!hist || !hist.warming) return;
    const timer = setTimeout(async () => {
      const hs = await window.glFetchFundflowHistory(kind, "");
      setHist(hs);
      if (hs && hs.ticks && hs.ticks.length && live && live.ok !== false) window.glSaveFundflowMemory(kind, { live, hist: hs });
    }, 5000);
    return () => clearTimeout(timer);
  }, [hist, kind, live]);

  if (live && live.ok === false) {
    return <main className="ff-page"><section className="ff-error-card"><span>!</span><div><h1>资金流向暂时断供</h1><p>{live.reason || (live.notes || []).join("；") || "数据源不可用"}</p><small>系统不会用估算值补齐真实资金流。</small></div></section></main>;
  }

  const notes = (live && live.notes) || [];
  const pulledAt = live ? String(live.pulled_at || "").slice(0, 16).replace("T", " ").replaceAll("/", "-") : "";
  return (
    <main className="ff-page">
      <header className="ff-hero" data-testid="fundflow-hero">
        <div className="ff-hero-copy">
          <div className="ff-kicker"><span /> FUND FLOW MONITOR</div>
          <div className="ff-title-line"><h1>资金流向</h1><span>板块主力资金全景</span></div>
          <p>观察资金正在流向哪里，以及哪些方向正在失血。纯展示参考，不构成交易信号。</p>
        </div>
        <div className="ff-hero-controls">
          <div className="ff-segmented" role="group" aria-label="板块类型">
            <button aria-pressed={kind === "concept"} onClick={() => setKind("concept")}>概念板块</button>
            <button aria-pressed={kind === "industry"} onClick={() => setKind("industry")}>行业板块</button>
          </div>
          <div className="ff-update-line">
            <div className="ff-data-time"><span className={live && live.trading ? "is-live" : "is-archive"} /><div><small>{fromMemory ? "本地记忆" : live && live.trading ? "盘中数据" : "盘后存档"}</small><strong>{pulledAt || "等待数据"}</strong></div></div>
            <button className="ff-refresh" onClick={() => update(kind)} disabled={loading}><span className={loading ? "is-spinning" : ""}>↻</span>{loading ? "更新中" : "更新数据"}</button>
          </div>
        </div>
      </header>

      <OverviewGrid live={live} fromMemory={fromMemory} />
      <MarketFlowBars m={(live && live.market) || {}} />
      <IntradayChart hist={hist} />
      <BoardRankTable boards={(live && live.boards) || []} />

      {notes.length > 0 && (
        <details className="ff-notes">
          <summary><span>i</span>数据口径与当前降级说明 <b>{notes.length}</b></summary>
          <div>{notes.map((n, i) => <p key={i}>{n}</p>)}</div>
        </details>
      )}
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
