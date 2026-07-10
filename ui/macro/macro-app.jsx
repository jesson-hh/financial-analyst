/* 观澜 · 全球情绪温度计 — 双半球:PM+Kalshi 全球宏观预期概率 × A股本土打板温度。
   纯展示层(绝不混入交易信号);单源失败/快照陈旧诚实显形。 */
const { useState, useEffect, useCallback } = React;

/* ── 温度色带:<35 青(risk-off) / 35-65 金(中性) / >65 朱(risk-on) ── */
function tempColor(t) {
  if (t == null) return { fg: "var(--ink-3)", wash: "var(--paper-sink)" };
  if (t < 35) return { fg: "var(--qing)", wash: "var(--qing-soft)" };
  if (t <= 65) return { fg: "var(--jin-deep)", wash: "var(--jin-wash)" };
  return { fg: "var(--zhu)", wash: "var(--zhu-wash)" };
}
const fmtTemp = (t) => (t == null ? "—" : Number(t).toFixed(1));
/* 0% 只该表示真的 0(而真 0 的已结算市场早被后端剔除)。0.2% 这类极低概率市场
   四舍五入成 0% 会被误读成幽灵行,故夹到 <1% / >99% 显示。 */
const fmtPct = (p) => {
  if (p == null) return "—";
  const v = p * 100;
  if (v > 0 && v < 0.5) return "<1%";
  if (v < 100 && v >= 99.5) return ">99%";
  return `${v.toFixed(0)}%`;
};
/* 展开区大数字:给出一位小数,极端值不夹断(读者点开就是要看精确值) */
const fmtPctPrecise = (p) => (p == null ? "—" : `${(p * 100).toFixed(1)}%`);

/* ── 总温度计仪表(横向色带+游标) ── */
function Gauge({ label, value, note }) {
  const c = tempColor(value);
  const pos = value == null ? 50 : Math.max(2, Math.min(98, value));
  return (
    <div style={{ flex: 1, minWidth: 260, background: "var(--paper-1)", border: "1px solid var(--line-2)",
                  borderRadius: 6, padding: "14px 18px" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <span style={{ fontFamily: "var(--font-serif)", fontSize: 14, color: "var(--ink-2)" }}>{label}</span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 28, fontWeight: 700, color: c.fg }}>{fmtTemp(value)}</span>
      </div>
      <div style={{ position: "relative", height: 10, marginTop: 10, borderRadius: 5, overflow: "hidden",
                    background: "linear-gradient(90deg, var(--qing-soft), var(--jin-wash) 50%, var(--zhu-soft))" }}>
        <div style={{ position: "absolute", left: `${pos}%`, top: 0, bottom: 0, width: 3,
                      background: value == null ? "var(--ink-4)" : "var(--ink-0)", transform: "translateX(-50%)" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4,
                    fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-3)" }}>
        <span>0 · risk-off</span><span>{note || ""}</span><span>risk-on · 100</span>
      </div>
    </div>
  );
}

/* ── 历史概率火花线(SVG polyline,无第三方库) ── */
function Spark({ hist }) {
  const pts = (hist || []).filter((h) => typeof h.prob === "number");
  if (pts.length < 2)
    return <div style={{ fontSize: 11, color: "var(--ink-3)", padding: "6px 0" }}>
      历史概率曲线待快照沉淀(每次刷新落一点,数日后成线)</div>;
  const W = 360, H = 56, P = 4;
  const xs = pts.map((_, i) => P + (i * (W - 2 * P)) / (pts.length - 1));
  const ys = pts.map((h) => H - P - h.prob * (H - 2 * P));
  return (
    <svg width={W} height={H} style={{ display: "block", marginTop: 4 }}>
      <polyline points={xs.map((x, i) => `${x},${ys[i]}`).join(" ")}
                fill="none" stroke="var(--zhu)" strokeWidth="1.6" />
      {xs.map((x, i) => <circle key={i} cx={x} cy={ys[i]} r="2" fill="var(--zhu-deep)" />)}
    </svg>
  );
}

/* ── 单市场行:概率条+Δ徽章+源徽章,点击展开历史+外链 ── */
function MarketRow({ m }) {
  const [open, setOpen] = useState(false);
  const [hist, setHist] = useState(null);
  const toggle = useCallback(async () => {
    const next = !open;
    setOpen(next);
    if (next && hist == null) setHist(await window.glFetchMacroHistory(m.id));
  }, [open, hist, m.id]);
  const d = m.delta24h;
  const dBadge = typeof d === "number"
    ? <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, fontWeight: 700,
                     color: d >= 0 ? "var(--zhu)" : "var(--dai)" }}>
        {d >= 0 ? "▲" : "▼"}{Math.abs(d * 100).toFixed(1)}pp</span>
    : <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-4)" }}>Δ—</span>;
  return (
    <div data-hv="row" onClick={toggle}
         style={{ padding: "6px 8px", borderTop: "1px solid var(--line-1)", cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, fontWeight: 700, padding: "1px 5px",
                       borderRadius: 3, color: "var(--text-on-ink)",
                       background: m.source === "polymarket" ? "var(--qing)" : "var(--dai)" }}>
          {m.source === "polymarket" ? "PM" : "K"}</span>
        {m.is_anchor ? (
          <span title="该市场参与本主题温度合成"
                style={{ fontFamily: "var(--font-mono)", fontSize: 9, fontWeight: 700, padding: "1px 5px",
                         borderRadius: 3, color: "var(--zhu)", border: "1px solid var(--zhu)" }}>锚</span>
        ) : null}
        <span style={{ flex: 1, fontSize: 12, color: "var(--ink-1)", overflow: "hidden",
                       textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={m.question}>
          {m.question_zh || m.question}
          {m.question_zh ? <span style={{ fontSize: 8, color: "var(--ink-4)", marginLeft: 4 }}>机翻</span> : null}
        </span>
        {dBadge}
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 700,
                       color: "var(--ink-0)", width: 42, textAlign: "right" }}>{fmtPct(m.prob)}</span>
      </div>
      <div style={{ height: 4, marginTop: 4, borderRadius: 2, background: "var(--paper-sink)" }}>
        <div style={{ width: `${(m.prob || 0) * 100}%`, height: "100%", borderRadius: 2,
                      background: m.source === "polymarket" ? "var(--qing)" : "var(--dai)" }} />
      </div>
      {open && (
        <div onClick={(e) => e.stopPropagation()}
             style={{ margin: "8px 0 4px", padding: "10px 12px", background: "var(--paper-2)",
                      border: "1px solid var(--line-1)", borderRadius: 4 }}>
          {m.question_zh && (
            <div style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 6 }}>原文:{m.question}</div>
          )}
          <div style={{ display: "flex", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 26, fontWeight: 700,
                           color: "var(--ink-0)" }}>{fmtPctPrecise(m.prob)}</span>
            <span style={{ fontSize: 11, color: "var(--ink-2)" }}>
              Δ24h {typeof d === "number" ? `${d >= 0 ? "+" : ""}${(d * 100).toFixed(1)}pp` : "—(需隔日快照)"}</span>
            <span style={{ fontSize: 11, color: "var(--ink-2)" }}>
              24h量 {m.volume ? `$${Math.round(m.volume).toLocaleString()}` : "—"}</span>
            <span style={{ fontSize: 11, color: "var(--ink-2)" }}>截止 {m.close_time || "—"}</span>
            {m.url ? (
              <a href={m.url} target="_blank" rel="noreferrer" data-hv="chip"
                 style={{ fontSize: 11, padding: "2px 10px", borderRadius: 3, textDecoration: "none",
                          background: "var(--qing-soft)", color: "var(--qing)",
                          border: "1px solid var(--line-2)" }}>去原市场 ↗</a>
            ) : null}
          </div>
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 2 }}>
              历史概率{hist && hist.length ? `(已沉淀 ${hist.length} 个快照点,跨数日后成趋势线)` : ""}</div>
            <Spark hist={hist} />
          </div>
        </div>
      )}
    </div>
  );
}

/* ── 主题卡片 ── */
function ThemeCard({ t }) {
  const c = tempColor(t.temp);
  return (
    <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6,
                  overflow: "hidden", animation: "glRise .3s ease both" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: "9px 12px", background: "var(--paper-0)", borderBottom: "1px solid var(--line-1)" }}>
        <span style={{ fontFamily: "var(--font-serif)", fontSize: 14, fontWeight: 600, color: "var(--ink-0)" }}>
          {t.label}</span>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-3)" }}>
            {t.anchor_hits != null ? `锚 ${t.anchor_hits}` : "快照"}</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 15, fontWeight: 700, color: c.fg,
                         background: c.wash, padding: "1px 8px", borderRadius: 4 }}>{fmtTemp(t.temp)}</span>
        </span>
      </div>
      <div>
        {(t.markets || []).length
          ? t.markets.map((m) => <MarketRow key={m.id} m={m} />)
          : <div style={{ padding: 12, fontSize: 11, color: "var(--ink-3)" }}>本主题本次无可用市场(源降级见页脚)</div>}
      </div>
    </div>
  );
}

/* ── A股本土温度(右半球) ── */
function AStockPanel({ a }) {
  if (!a || !a.available) {
    return (
      <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6, padding: 14 }}>
        <div style={{ fontFamily: "var(--font-serif)", fontSize: 15, fontWeight: 600, color: "var(--ink-0)" }}>
          A股本土温度</div>
        <div style={{ marginTop: 8, padding: 10, background: "var(--paper-sink)", borderRadius: 4,
                      fontSize: 11, color: "var(--ink-2)" }}>
          不可用:{((a && a.notes) || ["stocks probe 缺席"]).join(";")}</div>
      </div>
    );
  }
  const c = tempColor(a.temp);
  const stat = (k, v) => (
    <div style={{ flex: 1, textAlign: "center", padding: "8px 0" }}>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 18, fontWeight: 700, color: "var(--ink-0)" }}>{v}</div>
      <div style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 2 }}>{k}</div>
    </div>
  );
  return (
    <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6, padding: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <span style={{ fontFamily: "var(--font-serif)", fontSize: 15, fontWeight: 600, color: "var(--ink-0)" }}>
          A股本土温度 <span style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>打板口径 · 东财涨停池</span></span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 24, fontWeight: 700, color: c.fg }}>{fmtTemp(a.temp)}</span>
      </div>
      <div style={{ display: "flex", marginTop: 8, background: "var(--paper-0)", borderRadius: 4,
                    border: "1px solid var(--line-1)" }}>
        {stat("涨停家数", a.zt_count)}
        {stat("最高连板", `${a.max_streak}板`)}
        {stat("开板率", `${((a.break_ratio || 0) * 100).toFixed(0)}%`)}
      </div>
      {(a.top_reasons || []).length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 11, color: "var(--ink-2)", marginBottom: 4 }}>涨停题材</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
            {a.top_reasons.slice(0, 8).map((r, i) => (
              <span key={i} data-hv="chip" style={{ fontSize: 11, padding: "2px 8px", borderRadius: 10,
                    background: "var(--jin-wash)", color: "var(--jin-deep)", border: "1px solid var(--jin-soft)" }}>
                {r.reason || r.name || r.title || JSON.stringify(r).slice(0, 20)}</span>
            ))}
          </div>
        </div>
      )}
      {(a.hot_list || []).length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 11, color: "var(--ink-2)", marginBottom: 4 }}>同花顺热榜</div>
          {a.hot_list.slice(0, 10).map((h, i) => (
            <div key={i} style={{ display: "flex", gap: 8, padding: "3px 0", fontSize: 12,
                                  borderTop: i ? "1px solid var(--line-1)" : "none" }}>
              <span style={{ fontFamily: "var(--font-mono)", color: i < 3 ? "var(--zhu)" : "var(--ink-3)",
                             width: 16 }}>{i + 1}</span>
              <span style={{ color: "var(--ink-1)" }}>{h.name || h.stock_name || h.title || "—"}</span>
              {h.code ? <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)",
                                       alignSelf: "center" }}>{h.code}</span> : null}
            </div>
          ))}
        </div>
      )}
      <div style={{ marginTop: 10, fontSize: 9, color: "var(--ink-4)", lineHeight: 1.5 }}>
        温度=clamp(30 + 0.35×涨停数 + 3×最高连板 − 30×开板率, 0, 100),确定性算术,常数在 themes.yaml。</div>
    </div>
  );
}

/* ── 实时盘口快照(数据中台④):全市场微观,SWR 只读展示,绝不混入信号 ── */
function pickName(r) {
  return (r && (r.name || r.stock_name || r.title || r.concept || r.industry || r.code)) || "—";
}
function TapeStat({ k, v }) {
  return (
    <div style={{ flex: 1, textAlign: "center", padding: "8px 0", minWidth: 68 }}>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 17, fontWeight: 700, color: "var(--ink-0)" }}>{v}</div>
      <div style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 2 }}>{k}</div>
    </div>
  );
}
function TapeList({ title, rows, color }) {
  const items = (rows || []).slice(0, 6);
  return (
    <div style={{ flex: 1, minWidth: 176 }}>
      <div style={{ fontSize: 11, color: "var(--ink-2)", marginBottom: 4 }}>{title}</div>
      {items.length
        ? items.map((r, i) => (
            <div key={i} style={{ display: "flex", gap: 6, padding: "2px 0", fontSize: 12,
                                  borderTop: i ? "1px solid var(--line-1)" : "none" }}>
              <span style={{ fontFamily: "var(--font-mono)", width: 14,
                             color: i < 3 ? (color || "var(--zhu)") : "var(--ink-3)" }}>{i + 1}</span>
              <span style={{ color: "var(--ink-1)", overflow: "hidden", textOverflow: "ellipsis",
                             whiteSpace: "nowrap" }}>{pickName(r)}</span>
            </div>))
        : <div style={{ fontSize: 11, color: "var(--ink-3)" }}>—</div>}
    </div>
  );
}
function MarketTapePanel({ tape }) {
  if (!tape) return null;                       // 未加载先不占位(温度计已在)
  const head = (extra) => (
    <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
      <span style={{ fontFamily: "var(--font-serif)", fontSize: 15, fontWeight: 600, color: "var(--ink-0)" }}>
        实时盘口 <span style={{ fontSize: 10, color: "var(--ink-3)", fontWeight: 400 }}>全市场微观 · 只读展示</span></span>
      {extra}
    </div>
  );
  const shell = (inner) => (
    <div style={{ marginTop: 14, background: "var(--paper-1)", border: "1px solid var(--line-2)",
                  borderRadius: 6, padding: 14 }}>{inner}</div>
  );
  if (tape.ok === false) {
    return shell(<>{head(null)}<div style={{ padding: 10, background: "var(--paper-sink)", borderRadius: 4,
                    fontSize: 11, color: "var(--ink-2)" }}>盘口快照不可用:{tape.reason || "后端降级"}</div></>);
  }
  if (tape.warming) {
    return shell(<>{head(null)}<div style={{ padding: 10, background: "var(--paper-sink)", borderRadius: 4,
                    fontSize: 11, color: "var(--ink-2)" }}>盘口快照预热中(后台首拉已触发),稍后自动刷新。</div></>);
  }
  const d = tape.derived || {};
  const src = tape.sources || {};
  const rows = (id) => (src[id] && src[id].rows) || [];
  const stale = tape.freshness && tape.freshness.stale;
  const ageMin = tape.freshness && tape.freshness.overall_age_s != null
    ? Math.round(tape.freshness.overall_age_s / 60) : null;
  const ageBadge = (
    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10,
                   color: stale ? "var(--jin-deep)" : "var(--ink-3)" }}>
      {stale ? `⚠ ${ageMin} 分钟前(刷新中)` : `现拉 ${String(tape.pulled_at || "").slice(0, 16).replace("T", " ")}`}</span>
  );
  return shell(
    <>
      {head(ageBadge)}
      <div style={{ display: "flex", flexWrap: "wrap", background: "var(--paper-0)", borderRadius: 4,
                    border: "1px solid var(--line-1)" }}>
        <TapeStat k="涨停" v={d.zt_count != null ? d.zt_count : "—"} />
        <TapeStat k="最高连板" v={d.max_streak != null ? `${d.max_streak}板` : "—"} />
        <TapeStat k="炸板率" v={d.break_rate != null ? `${(d.break_rate * 100).toFixed(0)}%` : "—"} />
        <TapeStat k="晋级率" v={d.promotion_rate != null ? `${(d.promotion_rate * 100).toFixed(0)}%` : "—"} />
        <TapeStat k="开板率" v={d.break_ratio != null ? `${(d.break_ratio * 100).toFixed(0)}%` : "—"} />
        <TapeStat k="跌停" v={d.dt_count != null ? d.dt_count : "—"} />
        <TapeStat k="炸板池" v={d.zb_count != null ? d.zb_count : "—"} />
        <TapeStat k="北向净额(亿)" v={d.north_net != null ? d.north_net : "—"} />
      </div>
      <div style={{ display: "flex", gap: 16, marginTop: 12, flexWrap: "wrap" }}>
        <TapeList title="龙虎榜(全市场)" rows={rows("eastmoney_lhb")} color="var(--zhu)" />
        <TapeList title="人气榜" rows={rows("eastmoney_hot_rank")} color="var(--jin-deep)" />
        <TapeList title="行业涨幅榜" rows={rows("eastmoney_industry_comparison")} color="var(--qing)" />
      </div>
      <div style={{ marginTop: 10, fontSize: 9, color: "var(--ink-4)", lineHeight: 1.5 }}>
        走统一实时客户端聚合(SWR 保鲜,过期后台异步刷新,龄期显形);纯展示,绝不混入 v4/信号。</div>
    </>
  );
}

/* ── 主应用 ── */
function App() {
  const [pulse, setPulse] = useState(null);
  const [tape, setTape] = useState(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async (refresh) => {
    setLoading(true);
    setPulse(await window.glFetchMacroPulse(refresh));
    setLoading(false);
    window.glFetchMarketTape().then(setTape).catch(() => {});  // 盘口独立拉,不阻塞温度计
  }, []);
  useEffect(() => { load(true); }, [load]);   // 首开即现拉(秒级,顺手落快照沉淀历史)
  useEffect(() => {                            // 快照 warming → 12s 后自动重取一次(后台首拉完成)
    if (tape && tape.warming) {
      const id = setTimeout(() => window.glFetchMarketTape().then(setTape).catch(() => {}), 12000);
      return () => clearTimeout(id);
    }
  }, [tape]);

  if (pulse && pulse.ok === false) {
    return <div style={{ padding: 40, fontFamily: "var(--font-serif)", color: "var(--ink-2)" }}>
      全球情绪温度计断供:{pulse.reason || "未知原因"}</div>;
  }
  const th = (pulse && pulse.thermometer) || {};
  const notes = [...((pulse && pulse.notes) || []),
                 ...((pulse && pulse.astock && pulse.astock.notes) || [])];  // A股侧 note(如涨停数截断)一并显形
  return (
    <div style={{ maxWidth: 1280, margin: "0 auto", padding: "18px 22px 40px" }}>
      {/* 页头 */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
        <h1 style={{ fontFamily: "var(--font-serif)", fontSize: 22, fontWeight: 700, color: "var(--ink-0)",
                     margin: 0 }}>全球情绪温度计</h1>
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
          预测市场概率(Polymarket + Kalshi)观测全球宏观预期 · 纯展示参考,非交易信号</span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          {pulse && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10,
                           color: pulse.stale_minutes ? "var(--jin-deep)" : "var(--ink-3)" }}>
              {pulse.stale_minutes
                ? `快照态 · ${Math.round(pulse.stale_minutes)} 分钟前`
                : `现拉 ${String(pulse.pulled_at || "").slice(0, 16).replace("T", " ")}`}</span>
          )}
          <button data-hv="zhu" onClick={() => load(true)} disabled={loading}
                  style={{ fontFamily: "var(--font-serif)", fontSize: 12, padding: "5px 16px", cursor: "pointer",
                           background: "var(--paper-0)", color: "var(--ink-1)",
                           border: "1px solid var(--line-3)", borderRadius: 4 }}>
            {loading ? "拉取中…" : "刷新"}</button>
        </span>
      </div>

      {/* 双仪表 */}
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
        <Gauge label="全球预期温度(锚定市场合成)" value={th.global} note="预测市场" />
        <Gauge label="A股本土温度(打板口径)" value={th.astock} note="东财涨停池" />
      </div>

      {loading && !pulse
        ? <div style={{ padding: 60, textAlign: "center", color: "var(--ink-3)",
                        fontFamily: "var(--font-serif)" }}>正在现拉预测市场概率…</div>
        : (
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)", gap: 14 }}>
            {/* 左半球:全球主题 */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, alignContent: "start" }}>
              {((pulse && pulse.themes) || []).map((t) => <ThemeCard key={t.id} t={t} />)}
            </div>
            {/* 右半球:A股 */}
            <AStockPanel a={pulse && pulse.astock} />
          </div>
        )}

      {/* 实时盘口快照(数据中台④,全宽只读) */}
      <MarketTapePanel tape={tape} />

      {/* 降级条:源失败/快照提示,诚实显形 */}
      {notes.length > 0 && (
        <div style={{ marginTop: 16, padding: "8px 12px", background: "var(--paper-sink)",
                      border: "1px solid var(--line-2)", borderRadius: 4 }}>
          {notes.map((n, i) => (
            <div key={i} style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-2)",
                                  padding: "1px 0" }}>⚠ {n}</div>
          ))}
        </div>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
