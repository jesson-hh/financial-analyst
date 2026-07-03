/* 观澜 · AI投研 — 渲染层。数据全真:board ok:false 整页断供卡;单信号 null 显 “—”+title=reason,绝不编数。 */
const { useState, useEffect } = React;

function Badge({ label, val, warn }) {
  return <span className="chip" title={warn || ""} style={warn ? { borderColor: "var(--yin)", color: "var(--yin)" } : {}}>
    {label} <b className="num">{val == null ? "—" : val}</b></span>;
}

function fmtPct(x) { return x == null ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`; }

function NodeCard({ s, selected, onSelect }) {
  const q = s.quant || {}, r = s.research || {};
  if (s.adjacent) return <div className="node stub"><div className="nr"><span className="nm">{s.name} · 相邻链 ↗</span></div></div>;
  const mom = q.momentum20;
  return (
    <div className={`node q-${s.quadrant || "ll"}${selected ? " sel" : ""}`} onClick={() => onSelect(s.id)}>
      <div className="nr">
        <span className="nm">{s.name}</span>
        {s.stars > 0 && <span className="star">{"★".repeat(s.stars)}</span>}
        <span className={`mom ${mom > 0 ? "up" : mom < 0 ? "dn" : ""}`} title={q.reason || ""}>{fmtPct(mom)}</span>
      </div>
      <div className="sig">
        <div className="therm"><i style={{ width: `${Math.min(100, Math.max(4, ((mom || 0) * 100 + 50)))}%`,
          background: s.quadrant === "hh" ? "var(--zhu)" : s.quadrant === "lh" ? "var(--jin)" : s.quadrant === "hl" ? "var(--zhu-soft)" : "var(--ink-3)" }} /></div>
        <span className="rp">研{r.n30 == null ? "—" : r.n30}</span>
        <span className="lg">{(s.equity_logic || []).join("·")}</span>
      </div>
    </div>
  );
}

function App() {
  const [board, setBoard] = useState(null);
  const [sel, setSel] = useState("C2");
  const [detail, setDetail] = useState(null);
  const [ing, setIng] = useState(null);
  useEffect(() => { glFetchBoard(false).then(setBoard); glIngestState().then(setIng); }, []);
  useEffect(() => { if (sel) glFetchSegment(sel).then(setDetail); }, [sel]);
  if (!board) return <div style={{ padding: 40, color: "var(--ink-3)" }}>加载中…</div>;
  if (!board.ok) return <div style={{ padding: 40 }} className="serif">看板不可用:{board.reason}</div>;
  const groups = board.groups.map((g) => ({ ...g, segs: board.segments.filter((s) => s.group === g.id) }));
  const fresh = board.freshness || {};
  const corpusWarn = fresh.corpus && !fresh.corpus.ok ? fresh.corpus.reason : null;
  return (
    <div className="page">
      <div className="infobar">
        <Badge label="语料" val={fresh.corpus && fresh.corpus.latest_publish_ts} warn={corpusWarn} />
        <Badge label="行情" val={fresh.quote_date} />
        <Badge label="已抽取" val={fresh.extracted_total} />
        <Badge label="上次批处理" val={fresh.last_ingest_at} />
        <button className="btn-ingest" onClick={async () => { const r = await glStartIngest();
          alert(r.accepted ? "已受理,后台处理中" : `未受理:${r.reason || ""}`); glIngestState().then(setIng); }}>
          處理新研報{ing && ing.running ? " · 处理中…" : ""}</button>
        <button className="chip" onClick={() => glFetchBoard(true).then(setBoard)}>↻ 刷新</button>
      </div>
      <div className="drivers">{board.drivers.map((d) => (
        <div className="drv" key={d.id}><div className="n">{d.name} <i>{d.id}</i></div>
          <div className="v" style={{ fontSize: 10, color: "var(--ink-3)" }}>{(d.indicators || []).join(" · ")}</div></div>))}
      </div>
      <div className="river">{groups.map((g) => (
        <div className="col" key={g.id}>
          <div className="ghead"><span className="gname">{g.name}</span><span className="gsub">{g.id}</span></div>
          {g.segs.map((s) => <NodeCard key={s.id} s={s} selected={s.id === sel} onSelect={setSel} />)}
        </div>))}
      </div>
      <div className="nar">{board.narratives.map((n) => (
        <div className="narc" key={n.id}><div className="nh"><span className="nn">{n.name}</span>
          <span className="st">{n.status}</span></div>
          <div className="bar"><i style={{ width: `${n.temp == null ? 0 : n.temp}%`,
            background: (n.temp || 0) >= 70 ? "var(--zhu)" : (n.temp || 0) >= 45 ? "var(--jin)" : "var(--dai-soft)" }} /></div>
          <div className="meta"><span>{n.temp == null ? "—" : `${n.temp}°`}</span>
            <span className="plus">研+{n.plus7}</span><span className="minus">-{n.minus7}</span></div></div>))}
      </div>
      {detail && detail.ok && (
        <div className="detail">
          <div className="panel">
            <div className="ph"><span className="t">{detail.segment.name}</span>
              <span className="s">{detail.segment.logic}</span></div>
            <div style={{ padding: "10px 14px", fontSize: 12, lineHeight: 1.8 }}>
              {detail.segment.global && (<div>
                <div>国际:{detail.segment.global.intl}</div>
                <div>国内:<b style={{ color: "var(--zhu)" }}>{detail.segment.global.cn_position}</b></div>
                <div>壁垒:{detail.segment.global.moat}</div>
                <div>逻辑:<span className="lg">{(detail.segment.global.equity_logic || []).join("+")}</span> · {detail.segment.global.prospect}</div>
              </div>)}
            </div>
          </div>
          <div className="panel">
            <div className="ph"><span className="t">研报观点流</span><span className="r">近30日 {detail.opinions.length} 条</span></div>
            <div className="flow">{detail.opinions.length === 0 && <div style={{ color: "var(--ink-3)", fontSize: 12 }}>无研报覆盖</div>}
              {detail.opinions.map((o, i) => (
                <div className="op" key={i}>
                  <div className="oh"><span className={`stance ${o.stance === "多" ? "bull" : o.stance === "空" ? "bear" : "neut"}`}>{o.stance}</span>
                    <span className="strength">{"●".repeat(o.strength || 1)}</span>
                    <span className="org">{o.org} · {o.publish_ts}</span></div>
                  <div className="ttl">{o.title}</div>
                  {o.quote && <div className="quote">“{o.quote}”</div>}
                  {o.quote_dropped && <div style={{ fontSize: 9.5, color: "var(--ink-3)" }}>引句未过原文校验,已省略</div>}
                  <div className="of"><span className="trace">溯源 {o.doc_id}</span></div>
                </div>))}
            </div>
          </div>
          <div className="panel">
            <div className="ph"><span className="t">票池</span><span className="r">{(detail.stocks || []).length} 只</span></div>
            <div className="stbl"><table><thead><tr><th>个股</th><th>角色</th></tr></thead><tbody>
              {(detail.stocks || []).map((st) => (
                <tr key={st.code}><td>{st.name}<span className="code">{st.code}</span></td>
                  <td>{st.role}{st.note ? ` · ${st.note}` : ""}</td></tr>))}
            </tbody></table></div>
          </div>
        </div>)}
      <div className="foot"><span>数据:研报抽取 DeepSeek · 行情引擎产物 · 单字段缺失显示 — 不编数</span></div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
