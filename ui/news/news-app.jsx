/* 观澜 · 资讯 — 产业一手 RSS 雷达(12 赛道 108 源;自写 newsradar,不抄 redline 词表)。
   数据:GET /data/newsradar(SWR 非阻塞)。红线:RSS 是不可信外部文本,此页纯展示、绝不进信号,
   也不喂 LLM(接 LLM 属另议且须挂注入防御)。点标题在新标签打开原文。 */
const { useState, useEffect, useCallback, useRef } = React;
const GB = window.GUANLAN_BACKEND || "";

/* 12 赛道:hint → {名, 色}(与 newsradar_sources.json industries 对齐) */
const SECTORS = [
  { k: "ai", n: "AI / 大模型", c: "#ff5a1f" }, { k: "semi", n: "半导体 / 芯片", c: "#22d3ee" },
  { k: "robot", n: "机器人 / 自动化", c: "#14b8a6" }, { k: "auto", n: "汽车 / 新能源车", c: "#fb7185" },
  { k: "energy", n: "能源 / 新能源", c: "#84cc16" }, { k: "bio", n: "生物医药 / 创新药", c: "#ec4899" },
  { k: "space", n: "航天 / 太空", c: "#8b5cf6" }, { k: "security", n: "网络安全", c: "#ef4444" },
  { k: "tech", n: "科技 / 互联网", c: "#3b82f6" }, { k: "consumer", n: "消费电子 / 消费", c: "#a855f7" },
  { k: "macro", n: "财经 / 宏观", c: "#eab308" }, { k: "science", n: "科学 / 前沿", c: "#38bdf8" },
];
const SMAP = Object.fromEntries(SECTORS.map((s) => [s.k, s]));

function Item({ it }) {
  const s = SMAP[it.sector] || { n: it.sector, c: "var(--ink-3)" };
  return (
    <a href={it.url || "#"} target="_blank" rel="noopener noreferrer"
       style={{ display: "block", textDecoration: "none", padding: "10px 12px", borderRadius: 5,
                borderLeft: `3px solid ${s.c}`, background: "var(--paper-1)", border: "1px solid var(--line-2)",
                borderLeftWidth: 3, borderLeftColor: s.c }} data-hv="chip">
      <div style={{ fontFamily: "var(--font-serif)", fontSize: 14, color: "var(--ink-0)", lineHeight: 1.45 }}>{it.title}</div>
      <div style={{ marginTop: 5, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap",
                    fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>
        <span style={{ color: s.c, fontWeight: 600 }}>{s.n}</span>
        <span>{it.source}</span>
        <span>{it.time}</span>
      </div>
      {it.summary ? <div style={{ marginTop: 4, fontSize: 11, color: "var(--ink-3)", lineHeight: 1.5 }}>{it.summary}</div> : null}
    </a>
  );
}

function App() {
  const [data, setData] = useState(null);
  const [sel, setSel] = useState([]);        // 选中赛道 hint(空=全部)
  const [days, setDays] = useState(7);
  const pollRef = useRef(null);

  const load = useCallback(async (secs, dys) => {
    try {
      const qs = new URLSearchParams();
      if (secs && secs.length) qs.set("sectors", secs.join(","));
      qs.set("days", String(dys));
      const r = await fetch(GB + "/data/newsradar?" + qs.toString());
      const j = await r.json();
      setData(j);
      if (j.warming && !pollRef.current) {
        pollRef.current = setTimeout(() => { pollRef.current = null; load(secs, dys); }, 15000);
      }
    } catch (e) { setData({ warming: false, items: [], error: String(e) }); }
  }, []);
  useEffect(() => { load(sel, days); return () => pollRef.current && clearTimeout(pollRef.current); }, [load, sel, days]);

  const toggle = (k) => setSel((cur) => cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]);

  const items = (data && data.items) || [];
  const warming = data && data.warming;
  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: "18px 22px 40px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ fontFamily: "var(--font-serif)", fontSize: 20, fontWeight: 700, color: "var(--ink-0)", margin: 0 }}>
          产业资讯雷达</h1>
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>12 赛道 108 个公开一手 RSS/Atom 源 · 纯展示情报,不进信号</span>
        <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>
          {warming ? "预热中…" : (data ? `${items.length} 条 · 现拉 ${String(data.pulled_at || "").slice(0, 16).replace("T", " ")}` : "…")}
        </span>
      </div>

      {/* 赛道过滤 chips */}
      <div style={{ display: "flex", gap: 6, marginTop: 14, flexWrap: "wrap" }}>
        <button onClick={() => setSel([])} data-hv="chip"
                style={{ padding: "4px 11px", fontSize: 12, borderRadius: 12, cursor: "pointer",
                         fontFamily: "var(--font-serif)", background: sel.length === 0 ? "var(--ink-1)" : "var(--paper-1)",
                         color: sel.length === 0 ? "#fff" : "var(--ink-2)", border: "1px solid var(--line-2)" }}>全部</button>
        {SECTORS.map((s) => {
          const on = sel.includes(s.k);
          return (
            <button key={s.k} onClick={() => toggle(s.k)} data-hv="chip"
                    style={{ padding: "4px 11px", fontSize: 12, borderRadius: 12, cursor: "pointer",
                             fontFamily: "var(--font-serif)", background: on ? s.c : "var(--paper-1)",
                             color: on ? "#fff" : "var(--ink-2)", border: `1px solid ${on ? s.c : "var(--line-2)"}` }}>{s.n}</button>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>时窗</span>
        {[1, 3, 7, 14].map((d) => (
          <button key={d} onClick={() => setDays(d)} data-hv="chip"
                  style={{ padding: "2px 9px", fontSize: 11, borderRadius: 10, cursor: "pointer", fontFamily: "var(--font-mono)",
                           background: days === d ? "var(--jin-deep)" : "var(--paper-1)", color: days === d ? "#fff" : "var(--ink-3)",
                           border: "1px solid var(--line-2)" }}>{d}天</button>
        ))}
      </div>

      {warming && items.length === 0 ? (
        <div style={{ marginTop: 20, fontSize: 13, color: "var(--ink-3)" }}>
          资讯雷达预热中(后台首次抓取 108 个源,约 20~40 秒),抓完自动显示…</div>
      ) : items.length === 0 ? (
        <div style={{ marginTop: 20, fontSize: 13, color: "var(--ink-3)" }}>该赛道/时窗内暂无条目(单源不可达会诚实留空,不编造)。</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
          {items.map((it, i) => <Item key={(it.url || "") + i} it={it} />)}
        </div>
      )}
      <div style={{ marginTop: 18, fontSize: 9, color: "var(--ink-4)", lineHeight: 1.6 }}>
        源表 vendored 自 simonlin1212/Vibe-Research(MIT);抓取/解析为观澜自写,不含 redline 过滤。
        RSS 为不可信外部文本,此页纯展示、绝不进信号,亦不喂 LLM。</div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
