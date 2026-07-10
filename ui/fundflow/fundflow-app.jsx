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
      <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 6 }}>
        大盘资金
        {m && m.date && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)", marginLeft: 8 }}>
            {m.date} · 沪深合计</span>
        )}
        {m && m.src_host && String(m.src_host).indexOf("delay") >= 0 && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--jin)", marginLeft: 8 }}>
            延时源</span>
        )}
        {(!m || m.main_net == null) && (
          <span style={{ fontSize: 10, color: "var(--ink-3)", marginLeft: 8 }}>· 源不可用,不编造</span>
        )}
      </div>
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

/* y 轴取整到「好看的」刻度步长(1/2/2.5/3/4/5/10 × 10^n 亿)——档位太粗会把曲线压扁 */
function niceStep(maxAbsYi) {
  const raw = maxAbsYi / 2;                      // 目标:0 线两侧各约 2 格
  const pow = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1))));
  const n = raw / pow;
  const mult = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 3 ? 3 : n <= 4 ? 4 : n <= 5 ? 5 : 10;
  return mult * pow;
}

/* 标签防重叠:按 y 排序后强制最小行距,再回推防越界 */
function spreadLabels(items, top, bottom, gap) {
  const s = items.slice().sort((a, b) => a.y - b.y);
  for (let i = 1; i < s.length; i++)
    if (s[i].y - s[i - 1].y < gap) s[i].y = s[i - 1].y + gap;
  const overflow = s.length ? s[s.length - 1].y - bottom : 0;
  if (overflow > 0) for (let i = s.length - 1; i >= 0; i--) {
    s[i].y -= overflow;
    if (i > 0 && s[i].y - s[i - 1].y >= gap) break;
  }
  if (s.length && s[0].y < top) {
    const d = top - s[0].y;
    for (let i = 0; i < s.length; i++) s[i].y += d;
  }
  return s;
}

/* 盘中分钟多线图(纯 SVG)—— 数据为东财当日 09:31→15:00 分钟累计线 */
function IntradayChart({ hist }) {
  const boards = (hist && hist.boards) || [];
  const ticks = (hist && hist.ticks) || [];
  const mkt = ((hist && hist.market_series) || {}).main_net || [];
  if (ticks.length < 2 || !boards.length)
    return <div style={{ padding: 24, fontSize: 12, color: "var(--ink-3)", background: "var(--paper-1)",
                         border: "1px solid var(--line-2)", borderRadius: 6, marginBottom: 12 }}>
      {hist && hist.warming
        ? "首次拉取当日分钟线(20 条板块 + 大盘,约 25 秒),完成后自动出图…"
        : "暂无当日分钟线(非交易日 / 盘前 / 上游源降级)。开盘后自动出全天曲线。"}</div>;

  const W = 980, H = 420, PL = 52, PR = 150, PT = 14, PB = 26;
  const all = boards.flatMap((b) => b.series).concat(mkt).filter((v) => v != null);
  const maxAbs = Math.max(1, ...all.map((v) => Math.abs(v)));
  const stepYi = niceStep(maxAbs / YI);
  const spanYi = Math.max(stepYi, Math.ceil((maxAbs / YI) / stepYi) * stepYi);
  const x = (i) => PL + (i * (W - PL - PR)) / (ticks.length - 1);
  const y = (v) => PT + (H - PT - PB) * (0.5 - (v / (spanYi * YI)) * 0.5);

  const seg = (series) => {
    const parts = []; let cur = [];
    series.forEach((v, i) => {
      if (v == null) { if (cur.length > 1) parts.push(cur); cur = []; }
      else cur.push(`${x(i)},${y(v)}`);
    });
    if (cur.length > 1) parts.push(cur);
    return parts;
  };
  const lastIdx = (s) => s.reduce((acc, v, i) => (v != null ? i : acc), null);

  // y 轴刻度
  const gridYi = [];
  for (let v = -spanYi; v <= spanYi + 1e-9; v += stepYi) gridYi.push(Math.round(v * 100) / 100);
  // x 轴刻度:等距取 6 个真实时刻
  const xIdx = [];
  const nx = Math.min(6, ticks.length);
  for (let i = 0; i < nx; i++) xIdx.push(Math.round((i * (ticks.length - 1)) / (nx - 1)));

  const labels = spreadLabels(
    boards.map((b) => {
      const li = lastIdx(b.series);
      return li == null ? null : { name: b.name, v: b.series[li], x: x(li), y: y(b.series[li]) };
    }).filter(Boolean),
    PT + 6, H - PB - 2, 12
  );

  return (
    <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6,
                  padding: 10, marginBottom: 12, overflowX: "auto" }}>
      <svg width={W} height={H} style={{ display: "block" }}>
        {gridYi.map((g) => (
          <g key={g}>
            <line x1={PL} y1={y(g * YI)} x2={W - PR} y2={y(g * YI)}
                  stroke={g === 0 ? "var(--line-3)" : "var(--line-1)"} strokeWidth={g === 0 ? 1 : 0.5} />
            <text x={PL - 6} y={y(g * YI) + 3} fontSize="9" textAnchor="end" fill="var(--ink-3)"
                  style={{ fontFamily: "var(--font-mono)" }}>{g > 0 ? "+" : ""}{g}亿</text>
          </g>
        ))}
        {xIdx.map((i) => (
          <g key={i}>
            <line x1={x(i)} y1={PT} x2={x(i)} y2={H - PB} stroke="var(--line-1)" strokeWidth="0.5" />
            <text x={x(i)} y={H - PB + 13} fontSize="9" textAnchor="middle" fill="var(--ink-3)"
                  style={{ fontFamily: "var(--font-mono)" }}>{ticks[i]}</text>
          </g>
        ))}

        {/* 大盘主力:加粗虚线基准 */}
        {mkt.some((v) => v != null) && seg(mkt).map((pts, pi) => (
          <polyline key={"m" + pi} points={pts.join(" ")} fill="none" stroke="var(--ink-2)"
                    strokeWidth="1.8" strokeDasharray="5 3" opacity="0.55" />
        ))}

        {boards.map((b) => {
          const li = lastIdx(b.series);
          const col = flowColor(li != null ? b.series[li] : null);
          return (
            <g key={b.name}>
              {seg(b.series).map((pts, pi) => (
                <polyline key={pi} points={pts.join(" ")} fill="none" stroke={col}
                          strokeWidth="1.3" opacity="0.85" />
              ))}
            </g>
          );
        })}

        {/* 右缘标签:错开后用引导线连回曲线末点 */}
        {labels.map((L) => (
          <g key={L.name}>
            <line x1={L.x} y1={y(L.v)} x2={W - PR + 2} y2={L.y - 3} stroke={flowColor(L.v)}
                  strokeWidth="0.5" opacity="0.35" />
            <text x={W - PR + 5} y={L.y} fontSize="10" fill={flowColor(L.v)}
                  style={{ fontFamily: "var(--font-mono)" }}>{L.name} {fmtYi(L.v)}</text>
          </g>
        ))}
        <text x={W - PR + 5} y={H - 4} fontSize="9" fill="var(--ink-3)">— — 大盘主力</text>
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

  // 分钟线冷启动约 25s(后台拉),warming 期间轮询直到出图;之后走 60s SWR 缓存。
  useEffect(() => {
    if (!hist || !hist.warming) return;
    const t = setTimeout(async () => setHist(await window.glFetchFundflowHistory(kind, "")), 5000);
    return () => clearTimeout(t);
  }, [hist, kind]);

  if (live && live.ok === false)
    return <div style={{ padding: 40, fontFamily: "var(--font-serif)", color: "var(--ink-2)" }}>
      资金流向断供:{live.reason || (live.notes || []).join("；") || "数据源不可用"}</div>;

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
