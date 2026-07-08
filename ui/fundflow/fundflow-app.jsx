/* 观澜 · 板块资金流向 — 纯展示层(绝不混入交易信号)。红涨绿跌 A股口径。 */
const { useState, useEffect, useCallback } = React;

const YI = 1e8;
const fmtYi = (v) => (v == null ? "—" : (v / YI).toFixed(2) + "亿");
const flowColor = (v) => (v == null ? "var(--ink-3)" : v >= 0 ? "var(--zhu)" : "var(--dai)");

/* 涨跌头条 */
function BreadthStrip({ b }) {
  const cell = (label, up, down) => (
    <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginRight: 22 }}>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--zhu)" }}>涨 {up == null ? "—" : up}</span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--dai)" }}>跌 {down == null ? "—" : down}</span>
    </div>
  );
  const a = (b && b.allA) || {}, i = (b && b.industry) || {}, c = (b && b.concept) || {};
  return (
    <div style={{ display: "flex", flexWrap: "wrap", padding: "8px 12px", background: "var(--paper-1)",
                  border: "1px solid var(--line-2)", borderRadius: 6, marginBottom: 12 }}>
      {cell("全A", a.up, a.down)}{cell("行业", i.up, i.down)}{cell("概念", c.up, c.down)}
    </div>
  );
}

/* 大盘五档分解(水平条) */
function MarketFlowBars({ m }) {
  const items = [["超大单", m && m.super_net], ["大单", m && m.large_net],
                 ["中单", m && m.mid_net], ["小单", m && m.small_net], ["主力", m && m.main_net]];
  const max = Math.max(1, ...items.map(([, v]) => Math.abs(v || 0)));
  return (
    <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6,
                  padding: "10px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 6 }}>大盘资金</div>
      {items.map(([label, v]) => (
        <div key={label} style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0" }}>
          <span style={{ width: 44, fontSize: 12, color: "var(--ink-2)" }}>{label}</span>
          <div style={{ flex: 1, height: 12, position: "relative", background: "var(--paper-sink)", borderRadius: 3 }}>
            <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "var(--line-3)" }} />
            <div style={{ position: "absolute", top: 1, bottom: 1, borderRadius: 2, background: flowColor(v),
                          width: `${(Math.abs(v || 0) / max) * 50}%`,
                          left: (v || 0) >= 0 ? "50%" : undefined,
                          right: (v || 0) < 0 ? "50%" : undefined }} />
          </div>
          <span style={{ width: 74, textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12,
                         color: flowColor(v) }}>{fmtYi(v)}</span>
        </div>
      ))}
    </div>
  );
}

/* 盘中多线图(纯 SVG,放大版 Spark) */
function IntradayChart({ hist }) {
  const boards = (hist && hist.boards) || [];
  const ticks = (hist && hist.ticks) || [];
  if (ticks.length < 2 || !boards.length)
    return <div style={{ padding: 24, fontSize: 12, color: "var(--ink-3)", background: "var(--paper-1)",
                         border: "1px solid var(--line-2)", borderRadius: 6, marginBottom: 12 }}>
      盘中数据累计中(每次刷新落一点,开盘后逐步成线;开 GUANLAN_FUNDFLOW_POLL=1 全时段成线)</div>;
  const W = 900, H = 380, PL = 8, PR = 120, PT = 12, PB = 18;
  const all = boards.flatMap((b) => b.series).filter((v) => v != null);
  const maxAbs = Math.max(1, ...all.map((v) => Math.abs(v)));
  const x = (i) => PL + (i * (W - PL - PR)) / (ticks.length - 1);
  const y = (v) => PT + (H - PT - PB) * (0.5 - (v / maxAbs) * 0.5);
  const seg = (series) => {
    const parts = [];
    let cur = [];
    series.forEach((v, i) => {
      if (v == null) { if (cur.length) parts.push(cur); cur = []; }
      else cur.push(`${x(i)},${y(v)}`);
    });
    if (cur.length) parts.push(cur);
    return parts;
  };
  return (
    <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6,
                  padding: 10, marginBottom: 12, overflowX: "auto" }}>
      <svg width={W} height={H} style={{ display: "block" }}>
        <line x1={PL} y1={y(0)} x2={W - PR} y2={y(0)} stroke="var(--line-3)" strokeWidth="1" />
        {boards.map((b) => {
          const last = [...b.series].reverse().find((v) => v != null);
          const col = flowColor(last);
          const li = b.series.map((v, i) => (v == null ? null : i)).filter((i) => i != null).slice(-1)[0];
          return (
            <g key={b.name}>
              {seg(b.series).map((pts, pi) => (
                <polyline key={pi} points={pts.join(" ")} fill="none" stroke={col} strokeWidth="1.4" opacity="0.85" />
              ))}
              {li != null && (
                <text x={x(li) + 4} y={y(b.series[li]) + 3} fontSize="10" fill={col}
                      style={{ fontFamily: "var(--font-mono)" }}>{b.name} {fmtYi(last)}</text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* 板块排行榜 */
function BoardRankTable({ boards }) {
  const top = (boards || []).slice(0, 20);
  return (
    <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6, padding: "6px 12px" }}>
      <div style={{ fontSize: 12, color: "var(--ink-2)", padding: "4px 0 6px" }}>板块净流入排行(前 20)</div>
      {top.map((b) => (
        <div key={b.code || b.name} style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0",
                                             borderTop: "1px solid var(--line-1)", fontSize: 12 }}>
          <span style={{ width: 20, fontFamily: "var(--font-mono)", color: "var(--ink-3)" }}>{b.rank}</span>
          <span style={{ flex: 1, color: "var(--ink-1)" }}>{b.name}</span>
          {typeof b.delta_intraday === "number" && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: flowColor(b.delta_intraday) }}>
              {b.delta_intraday >= 0 ? "▲" : "▼"}{fmtYi(Math.abs(b.delta_intraday))}</span>
          )}
          <span style={{ fontFamily: "var(--font-mono)", color: flowColor(b.change_pct) }}>
            {b.change_pct >= 0 ? "+" : ""}{Number(b.change_pct).toFixed(2)}%</span>
          <span style={{ width: 82, textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700,
                         color: flowColor(b.main_net) }}>{fmtYi(b.main_net)}</span>
        </div>
      ))}
    </div>
  );
}

function App() {
  const [kind, setKind] = useState("concept");
  const [live, setLive] = useState(null);
  const [hist, setHist] = useState(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async (k, refresh) => {
    setLoading(true);
    const [lv, hs] = await Promise.all([window.glFetchFundflowLive(k, refresh),
                                        window.glFetchFundflowHistory(k, "")]);
    setLive(lv); setHist(hs); setLoading(false);
  }, []);
  useEffect(() => { load(kind, true); }, [kind, load]);

  if (live && live.ok === false && live.reason)
    return <div style={{ padding: 40, fontFamily: "var(--font-serif)", color: "var(--ink-2)" }}>
      资金流向断供:{live.reason}</div>;

  const notes = (live && live.notes) || [];
  const tab = (k, label) => (
    <button onClick={() => setKind(k)} data-hv="zhu"
      style={{ fontFamily: "var(--font-serif)", fontSize: 13, padding: "4px 14px", cursor: "pointer",
               background: kind === k ? "var(--zhu)" : "var(--paper-0)",
               color: kind === k ? "var(--text-on-ink)" : "var(--ink-1)",
               border: "1px solid var(--line-3)", borderRadius: 4 }}>{label}</button>
  );
  return (
    <div style={{ maxWidth: 1180, margin: "0 auto", padding: "18px 22px 40px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <h1 style={{ fontFamily: "var(--font-serif)", fontSize: 22, fontWeight: 700, color: "var(--ink-0)", margin: 0 }}>
          板块资金流向</h1>
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>盘中主力净流入 · 纯展示参考,非交易信号</span>
        <span style={{ display: "flex", gap: 6, marginLeft: 8 }}>{tab("concept", "概念")}{tab("industry", "行业")}</span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          {live && <span style={{ fontFamily: "var(--font-mono)", fontSize: 10,
                                  color: live.trading ? "var(--zhu)" : "var(--ink-3)" }}>
            {live.trading ? "盘中" : "非交易"} · {String(live.pulled_at || "").slice(0, 16).replace("T", " ")}</span>}
          <button onClick={() => load(kind, true)} disabled={loading} data-hv="zhu"
            style={{ fontFamily: "var(--font-serif)", fontSize: 12, padding: "5px 16px", cursor: "pointer",
                     background: "var(--paper-0)", color: "var(--ink-1)", border: "1px solid var(--line-3)", borderRadius: 4 }}>
            {loading ? "拉取中…" : "刷新"}</button>
        </span>
      </div>

      <BreadthStrip b={live && live.breadth} />
      <MarketFlowBars m={(live && live.market) || {}} />
      <IntradayChart hist={hist} />
      <BoardRankTable boards={(live && live.boards) || []} />

      {notes.length > 0 && (
        <div style={{ marginTop: 14, padding: "8px 12px", background: "var(--paper-sink)",
                      border: "1px solid var(--line-2)", borderRadius: 4 }}>
          {notes.map((n, i) => (
            <div key={i} style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-2)", padding: "1px 0" }}>⚠ {n}</div>
          ))}
        </div>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
