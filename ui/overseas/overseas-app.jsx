/* 观澜 · 海外 — 美股/港股个股行情看板(SWR 秒回,纯展示,绝不混入 A 股信号)。
   数据:GET /data/overseas(精选清单)/ ?code=(单只 lookup)。红涨绿跌(A 股用户习惯)。 */
const { useState, useEffect, useCallback, useRef } = React;
const GB = window.GUANLAN_BACKEND || "";

const upDown = (v) => (v == null ? "var(--ink-3)" : v > 0 ? "var(--zhu)" : v < 0 ? "var(--dai)" : "var(--ink-2)");
const fnum = (v, d = 2) => (v == null || v === "" || isNaN(v) ? "—" : Number(v).toFixed(d));
const fpct = (v) => (v == null || isNaN(v) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`);
const fvol = (v) => {
  if (v == null || isNaN(v)) return "—";
  const n = Number(v);
  if (n >= 1e8) return (n / 1e8).toFixed(2) + "亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + "万";
  return String(n);
};

function QuoteCard({ q }) {
  const dn = q.price == null;
  return (
    <div style={{ flex: "1 1 200px", minWidth: 190, background: "var(--paper-1)",
                  border: "1px solid var(--line-2)", borderRadius: 6, padding: "12px 14px" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <span style={{ fontFamily: "var(--font-serif)", fontSize: 14, fontWeight: 600, color: "var(--ink-1)" }}>
          {q.label || q.name || q.code}</span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-3)",
                       border: "1px solid var(--line-2)", borderRadius: 3, padding: "0 4px" }}>{q.market}</span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginTop: 8 }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 22, fontWeight: 700, color: upDown(q.change_pct) }}>
          {fnum(q.price)}</span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: upDown(q.change_pct) }}>{fpct(q.change_pct)}</span>
      </div>
      <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)", lineHeight: 1.6 }}>
        {dn ? <span style={{ color: "var(--ink-4)" }}>{q.note || "缺价/不可达"}</span>
            : <>{q.code} · 昨收 {fnum(q.prev_close)} · 高 {fnum(q.high)} · 低 {fnum(q.low)} · 量 {fvol(q.volume)}</>}
      </div>
    </div>
  );
}

function App() {
  const [data, setData] = useState(null);
  const [lookup, setLookup] = useState("");
  const [hit, setHit] = useState(null);
  const [hitErr, setHitErr] = useState("");
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(GB + "/data/overseas");
      const j = await r.json();
      setData(j);
      if (j.warming && !pollRef.current) {          // warming → 12s 后自动重取(后台首拉完成)
        pollRef.current = setTimeout(() => { pollRef.current = null; load(); }, 12000);
      }
    } catch (e) { setData({ warming: false, rows: [], error: String(e) }); }
  }, []);
  useEffect(() => { load(); return () => pollRef.current && clearTimeout(pollRef.current); }, [load]);

  const doLookup = useCallback(async () => {
    const code = lookup.trim();
    if (!code) return;
    setHit(null); setHitErr("");
    try {
      const r = await fetch(GB + "/data/overseas?code=" + encodeURIComponent(code));
      const j = await r.json();
      if (j.ok && j.row) setHit(j.row); else setHitErr("查无此票或暂不可达:" + code);
    } catch (e) { setHitErr(String(e)); }
  }, [lookup]);

  const rows = (data && data.rows) || [];
  const warming = data && data.warming;
  const stale = data && data.stale;
  return (
    <div style={{ maxWidth: 1180, margin: "0 auto", padding: "18px 22px 40px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ fontFamily: "var(--font-serif)", fontSize: 20, fontWeight: 700, color: "var(--ink-0)", margin: 0 }}>
          海外行情</h1>
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>美股 / 港股个股 · 腾讯境内端点 · 纯展示参考,非交易信号</span>
        <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>
          {warming ? "预热中…" : (data ? `现拉 ${String(data.pulled_at || "").slice(0, 16).replace("T", " ")}${stale ? " ·刷新中" : ""}` : "…")}
        </span>
      </div>

      {/* 个股 lookup */}
      <div style={{ display: "flex", gap: 8, marginTop: 14, alignItems: "center", flexWrap: "wrap" }}>
        <input value={lookup} onChange={(e) => setLookup(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && doLookup()}
               placeholder="输入代码查任意海外票:AAPL / TSLA / 00700 / 09988"
               style={{ flex: "1 1 320px", maxWidth: 420, padding: "8px 12px", fontSize: 13,
                        fontFamily: "var(--font-mono)", background: "var(--paper-1)",
                        border: "1px solid var(--line-2)", borderRadius: 5, color: "var(--ink-1)" }} />
        <button onClick={doLookup} data-hv="zhu"
                style={{ padding: "8px 16px", fontSize: 13, fontFamily: "var(--font-serif)", cursor: "pointer",
                         background: "var(--zhu)", color: "#fff", border: "none", borderRadius: 5 }}>查询</button>
      </div>
      {hitErr && <div style={{ marginTop: 8, fontSize: 12, color: "var(--dai)" }}>{hitErr}</div>}
      {hit && <div style={{ display: "flex", marginTop: 10 }}><QuoteCard q={hit} /></div>}

      {/* 精选清单 */}
      <div style={{ marginTop: 20, fontFamily: "var(--font-serif)", fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>
        精选盯盘池 · 美股权重 + 中概龙头</div>
      {warming && rows.length === 0 ? (
        <div style={{ marginTop: 16, fontSize: 13, color: "var(--ink-3)" }}>海外行情预热中(后台首拉已触发),约 10 秒后自动显示…</div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 12 }}>
          {rows.map((q) => <QuoteCard key={q.code + q.market} q={q} />)}
        </div>
      )}
      <div style={{ marginTop: 18, fontSize: 9, color: "var(--ink-4)", lineHeight: 1.6 }}>
        经统一实时门户 live_client 逐只聚合(SWR 保鲜,过期后台异步刷新);红涨绿跌;纯展示,绝不混入 A 股 v4/信号。</div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
