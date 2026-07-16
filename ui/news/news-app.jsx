/* 观澜 · 资讯 —《产业情报》邸报式重设计。文人书案语言:宣纸底 + 朱砂印 + 衬线大标题 + 赛道色带。
   双视图:赛道墙(杂志分栏 masonry)/ 时间流(编年河)。数据 GET /data/newsradar(SWR)。
   红线:RSS 是不可信外部文本 —— 纯展示,绝不进信号、不喂 LLM(点标题在新标签开原文)。 */
const { useState, useEffect, useCallback, useRef, useMemo } = React;
const GB = window.GUANLAN_BACKEND || "";

/* 12 赛道:hint → {名, 色, 简}(与 newsradar_sources.json industries 对齐;色沿设计稿 accent) */
const SECTORS = [
  { k: "ai", n: "AI · 大模型", c: "#B23A2B", z: "智" },
  { k: "semi", n: "半导体 · 芯片", c: "#3C5C68", z: "芯" },
  { k: "robot", n: "机器人 · 自动化", c: "#3E6152", z: "械" },
  { k: "auto", n: "汽车 · 新能源车", c: "#97301F", z: "车" },
  { k: "energy", n: "能源 · 新能源", c: "#7E5F10", z: "能" },
  { k: "bio", n: "生物医药 · 创新药", c: "#8A4B5C", z: "药" },
  { k: "space", n: "航天 · 太空", c: "#4A4668", z: "天" },
  { k: "security", n: "网络安全", c: "#8C3A2E", z: "盾" },
  { k: "tech", n: "科技 · 互联网", c: "#365A6E", z: "网" },
  { k: "consumer", n: "消费电子 · 消费", c: "#7A5A2E", z: "消" },
  { k: "macro", n: "财经 · 宏观", c: "#A37D17", z: "宏" },
  { k: "science", n: "科学 · 前沿", c: "#3C5C68", z: "研" },
];
const SMAP = Object.fromEntries(SECTORS.map((s) => [s.k, s]));
const sect = (k) => SMAP[k] || { n: k, c: "#998C6F", z: "·" };

const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return ""; } };

/* ── 报头 nameplate:朱砂印 + 双线 + 报眉 ── */
function Masthead({ n, pulledAt, warming, onRefresh, refreshing }) {
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 14 }}>
        <div style={{ width: 46, height: 46, background: "var(--zhu)", color: "var(--text-on-zhu)",
                      fontFamily: "var(--font-serif)", fontWeight: 700, fontSize: 26, borderRadius: 3,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      boxShadow: "1px 2px 0 rgba(33,27,18,.18)", flex: "0 0 auto" }}>報</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <h1 style={{ margin: 0, fontFamily: "var(--font-serif)", fontWeight: 900, fontSize: 34,
                       letterSpacing: ".22em", color: "var(--ink-0)", lineHeight: 1 }}>產業情報</h1>
          <span style={{ fontFamily: "var(--font-serif)", fontSize: 12, letterSpacing: ".3em",
                         color: "var(--ink-3)" }}>觀瀾 · 資訊雷達</span>
        </div>
        <button onClick={onRefresh} data-hv="chip" title="更新数据"
                style={{ marginLeft: "auto", alignSelf: "center", padding: "6px 14px", cursor: "pointer",
                         fontFamily: "var(--font-serif)", fontSize: 12.5, letterSpacing: ".08em",
                         background: "var(--paper-2)", color: "var(--ink-2)", border: "1px solid var(--line-2)",
                         borderRadius: 3 }}>{refreshing ? "刷新中…" : "更新"}</button>
      </div>
      {/* 报纸双线 */}
      <div style={{ marginTop: 12, borderTop: "2px solid var(--ink-1)", borderBottom: "1px solid var(--line-3)",
                    padding: "5px 1px", display: "flex", alignItems: "center", gap: 14,
                    fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)", letterSpacing: ".04em" }}>
        <span style={{ color: "var(--zhu-deep)", fontWeight: 600 }}>第 {n} 条</span>
        <span>12 赛道 · 108 个公开一手 RSS/Atom 源</span>
        <span style={{ marginLeft: "auto" }}>
          {warming ? "◷ 预热中…" : `現拉 ${String(pulledAt || "").slice(0, 16).replace("T", " ")}`}
        </span>
      </div>
    </div>
  );
}

/* ── 单条(赛道墙紧凑 / 时间流展开) ── */
function Story({ it, mode, idx }) {
  const s = sect(it.sector);
  const river = mode === "river";
  return (
    <a href={it.url || "#"} target="_blank" rel="noopener noreferrer"
       style={{ display: "block", textDecoration: "none", color: "inherit",
                padding: river ? "13px 16px 13px 18px" : "9px 2px",
                borderLeft: river ? `3px solid ${s.c}` : "none",
                borderBottom: river ? "none" : "1px dashed var(--line-1)",
                background: river ? "var(--paper-1)" : "transparent",
                borderRadius: river ? 4 : 0,
                animation: "glRise .5s both", animationDelay: `${Math.min(idx, 24) * 22}ms` }}
       data-hv={river ? "drv" : "row"}>
      <div style={{ fontFamily: "var(--font-serif)", fontWeight: river ? 600 : 500,
                    fontSize: river ? 16 : 13.5, lineHeight: 1.42, color: "var(--ink-0)",
                    letterSpacing: ".005em" }}>{it.title}</div>
      <div style={{ marginTop: 5, display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap",
                    fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>
        {river && <span style={{ color: s.c, fontFamily: "var(--font-serif)", fontWeight: 600, letterSpacing: ".05em" }}>{s.n}</span>}
        <span style={{ color: "var(--ink-2)" }}>{it.source}</span>
        {hostOf(it.url) && <span style={{ color: "var(--ink-4)" }}>{hostOf(it.url)}</span>}
        <span style={{ marginLeft: "auto", color: "var(--ink-3)" }}>{it.time}</span>
      </div>
      {river && it.summary ? (
        <div style={{ marginTop: 6, fontSize: 12, color: "var(--ink-2)", lineHeight: 1.6 }}>{it.summary}</div>
      ) : null}
    </a>
  );
}

/* ── 赛道栏(墙视图):色带报头 + 该赛道条目 ── */
function SectorColumn({ s, items, onOpen, idx }) {
  const shown = items.slice(0, 8);
  return (
    <section style={{ breakInside: "avoid", marginBottom: 18, background: "var(--paper-1)",
                      border: "1px solid var(--line-2)", borderTop: `3px solid ${s.c}`, borderRadius: "2px 2px 5px 5px",
                      boxShadow: "1px 2px 0 rgba(33,27,18,.05)", overflow: "hidden",
                      animation: "glRise .55s both", animationDelay: `${idx * 45}ms` }}>
      <header onClick={() => onOpen(s.k)} data-hv="soft"
              style={{ display: "flex", alignItems: "center", gap: 9, padding: "9px 13px", cursor: "pointer",
                       background: "var(--paper-2)", borderBottom: "1px solid var(--line-1)" }}>
        <span style={{ width: 21, height: 21, borderRadius: 2, background: s.c, color: "#F7ECE6",
                       fontFamily: "var(--font-serif)", fontSize: 13, fontWeight: 700,
                       display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 auto" }}>{s.z}</span>
        <span style={{ fontFamily: "var(--font-serif)", fontWeight: 700, fontSize: 14, color: "var(--ink-0)",
                       letterSpacing: ".03em" }}>{s.n}</span>
        <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 11, color: s.c, fontWeight: 600 }}>
          {items.length}</span>
      </header>
      <div style={{ padding: "4px 13px 9px" }}>
        {shown.map((it, i) => <Story key={(it.url || "") + i} it={it} mode="wall" idx={i} />)}
        {items.length > shown.length && (
          <button onClick={() => onOpen(s.k)} data-hv="chip"
                  style={{ marginTop: 7, padding: "3px 10px", fontSize: 11, cursor: "pointer",
                           fontFamily: "var(--font-serif)", background: "transparent", color: s.c,
                           border: `1px solid ${s.c}44`, borderRadius: 11 }}>
            +{items.length - shown.length} 更多 →</button>
        )}
      </div>
    </section>
  );
}

/* ── 分段控件 ── */
function Seg({ opts, val, onChange }) {
  return (
    <div style={{ display: "inline-flex", border: "1px solid var(--line-2)", borderRadius: 4, overflow: "hidden" }}>
      {opts.map((o, i) => {
        const on = o.v === val;
        return (
          <button key={o.v} onClick={() => onChange(o.v)} data-hv={on ? undefined : "chip"}
                  style={{ padding: "6px 15px", fontSize: 12.5, cursor: "pointer", fontFamily: "var(--font-serif)",
                           letterSpacing: ".05em", border: "none", borderLeft: i ? "1px solid var(--line-2)" : "none",
                           background: on ? "var(--ink-1)" : "var(--paper-1)", color: on ? "var(--paper-0)" : "var(--ink-2)" }}>
            {o.t}</button>
        );
      })}
    </div>
  );
}

function App() {
  const [data, setData] = useState(null);
  const [view, setView] = useState("wall");        // wall | river
  const [days, setDays] = useState(7);
  const [sel, setSel] = useState([]);              // 选中赛道(空=全部)
  const [refreshing, setRefreshing] = useState(false);
  const pollRef = useRef(null);

  const load = useCallback(async (dys) => {
    try {
      const r = await fetch(`${GB}/data/newsradar?days=${dys}&limit=500`);
      const j = await r.json();
      setData(j); setRefreshing(false);
      if (j.warming && !pollRef.current) {
        pollRef.current = setTimeout(() => { pollRef.current = null; load(dys); }, 15000);
      }
    } catch (e) { setData({ warming: false, items: [], error: String(e) }); setRefreshing(false); }
  }, []);
  useEffect(() => { load(days); return () => pollRef.current && clearTimeout(pollRef.current); }, [load, days]);

  const items = (data && data.items) || [];
  const filtered = useMemo(
    () => (sel.length ? items.filter((it) => sel.includes(it.sector)) : items), [items, sel]);
  const bySector = useMemo(() => {
    const m = {};
    filtered.forEach((it) => { (m[it.sector] = m[it.sector] || []).push(it); });
    return SECTORS.map((s) => ({ s, items: m[s.k] || [] })).filter((g) => g.items.length)
      .sort((a, b) => b.items.length - a.items.length);
  }, [filtered]);

  const warming = data && data.warming;
  const toggle = (k) => setSel((c) => c.includes(k) ? c.filter((x) => x !== k) : [...c, k]);
  const openSector = (k) => { setView("river"); setSel([k]); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const refresh = () => { setRefreshing(true); load(days); };

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "20px 24px 48px" }}>
      <Masthead n={items.length} pulledAt={data && data.pulled_at} warming={warming}
                onRefresh={refresh} refreshing={refreshing} />

      {/* 控制条 */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", margin: "16px 0 4px" }}>
        <Seg opts={[{ v: "wall", t: "赛道墙" }, { v: "river", t: "时间流" }]} val={view} onChange={setView} />
        <Seg opts={[{ v: 1, t: "今日" }, { v: 3, t: "三日" }, { v: 7, t: "七日" }, { v: 14, t: "双周" }]}
             val={days} onChange={setDays} />
        {sel.length > 0 && (
          <button onClick={() => setSel([])} data-hv="chip"
                  style={{ padding: "5px 12px", fontSize: 12, cursor: "pointer", fontFamily: "var(--font-serif)",
                           background: "var(--zhu-wash)", color: "var(--zhu-deep)", border: "1px solid var(--zhu-soft)",
                           borderRadius: 12 }}>✕ 清除筛选({sel.length})</button>
        )}
        <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>
          {filtered.length} 条</span>
      </div>

      {/* 赛道筛选带 */}
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginTop: 10, paddingBottom: 14,
                    borderBottom: "1px solid var(--line-1)" }}>
        {SECTORS.map((s) => {
          const on = sel.includes(s.k);
          return (
            <button key={s.k} onClick={() => toggle(s.k)} data-hv="chip"
                    style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 11px", cursor: "pointer",
                             fontFamily: "var(--font-serif)", fontSize: 12, borderRadius: 13,
                             background: on ? s.c : "var(--paper-1)", color: on ? "#F7ECE6" : "var(--ink-2)",
                             border: `1px solid ${on ? s.c : "var(--line-2)"}` }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: on ? "#F7ECE6" : s.c }} />
              {s.n}</button>
          );
        })}
      </div>

      {/* 内容 */}
      {warming && items.length === 0 ? (
        <div style={{ marginTop: 40, textAlign: "center", fontFamily: "var(--font-serif)", fontSize: 14, color: "var(--ink-3)" }}>
          ◷ 情报雷达预热中<br />
          <span style={{ fontSize: 12, color: "var(--ink-4)" }}>后台首次扫描 108 个源(约 20~40 秒),抓完自动上报…</span>
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ marginTop: 40, textAlign: "center", fontSize: 13, color: "var(--ink-3)" }}>
          该赛道 / 时窗内暂无条目(单源不可达会诚实留空,不编造)。</div>
      ) : view === "wall" ? (
        <div style={{ marginTop: 18, columnWidth: 320, columnGap: 18 }}>
          {bySector.map((g, i) => <SectorColumn key={g.s.k} s={g.s} items={g.items} onOpen={openSector} idx={i} />)}
        </div>
      ) : (
        <div style={{ marginTop: 18, display: "flex", flexDirection: "column", gap: 9, maxWidth: 760 }}>
          {filtered.map((it, i) => <Story key={(it.url || "") + i} it={it} mode="river" idx={i} />)}
        </div>
      )}

      <div style={{ marginTop: 28, paddingTop: 12, borderTop: "1px solid var(--line-1)",
                    fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-4)", lineHeight: 1.7 }}>
        源表 vendored 自 simonlin1212/Vibe-Research(MIT);抓取 / 解析为观澜自写,不含 redline 过滤。
        RSS 为不可信外部文本 —— 本页纯展示、绝不进信号,亦不喂 LLM。红涨绿跌与情绪/信号层严格隔离。</div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
