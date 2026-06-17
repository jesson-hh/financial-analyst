// 极客风 · 甲 · 流 — 终端美学 / 暖色暗调
// 1440 x 900
// 灵感：现代化的 TUI（Linear + Vercel + Codex），但保留 ASCII 美感
// 几乎全等宽字体；用 [ ] · ─ → 等符号造结构；琥珀色高亮，CRT 弱辉光

const GeekStream = () => {
  const W = 1440, H = 1300;

  return (
    <div className="geek-scope" style={{
      width: W, height: H, display: 'flex',
      fontFamily: '"JetBrains Mono", "IBM Plex Mono", monospace',
      color: 'var(--g-ink)', background: 'var(--g-bg)',
      fontFeatureSettings: '"ss01","ss02","cv11"'
    }}>

      {/* ═════ 左栏 ═════ */}
      <aside style={{ width: 260, borderRight: '1px solid var(--g-line)', display: 'flex', flexDirection: 'column', flexShrink: 0, background: 'var(--g-surface)' }}>

        {/* 品牌 — ASCII */}
        <div style={{ padding: '20px 18px 18px', borderBottom: '1px solid var(--g-line)' }}>
          <pre style={{ margin: 0, fontSize: 10, color: 'var(--g-amber)', lineHeight: 1.1 }}>{`
 ▄▄·▄▄▄▄▄ ▄▄▌
▐█ ▌▪•██  ▐█·
██ ▄▄ ▐█.▪▐█·
▐███▌ ▐█▌·▐█▌
·▀▀▀  ▀▀▀ ▀▀▀`}</pre>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 6 }}>
            <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: '0.05em', color: 'var(--g-ink)' }}>ctl</span>
            <span style={{ fontSize: 10, color: 'var(--g-ink-3)' }}>v1.8.3</span>
            <span style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--g-down)' }}>● ready</span>
          </div>
        </div>

        {/* 新对话 */}
        <div style={{ padding: '14px 18px 8px' }}>
          <button style={{
            width: '100%', padding: '9px 12px', background: 'transparent', color: 'var(--g-amber)',
            border: '1px solid var(--g-amber)', fontFamily: 'inherit', fontSize: 11, letterSpacing: '0.08em', cursor: 'pointer',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center'
          }}>
            <span>[+] new chat</span>
            <span style={{ color: 'var(--g-ink-3)', fontSize: 10 }}>⌘N</span>
          </button>
        </div>

        {/* sections */}
        <GeekRail label="watchlist" count="6">
          {[
            { n: '宁德时代', c: '300750', p: '325.10', d: '+2.21' },
            { n: '贵州茅台', c: '600519', p: '1684.0', d: '-0.42' },
            { n: '比亚迪',   c: '002594', p: '281.40', d: '+1.68' },
            { n: '中际旭创', c: '300308', p: '142.55', d: '+4.12' },
            { n: '隆基绿能', c: '601012', p: '17.84',  d: '-1.06' },
          ].map((r, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', padding: '4px 18px', fontSize: 11, gap: 6 }}>
              <span style={{ width: 56, color: 'var(--g-ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.n}</span>
              <span style={{ color: 'var(--g-ink-3)', fontSize: 9, width: 42 }}>{r.c}</span>
              <span style={{ flex: 1, textAlign: 'right', color: 'var(--g-ink-2)' }}>{r.p}</span>
              <span style={{ width: 44, textAlign: 'right', color: r.d.startsWith('-') ? 'var(--g-down)' : 'var(--g-up)' }}>{r.d}%</span>
            </div>
          ))}
        </GeekRail>

        <GeekRail label="watch.alerts" count="3 active" countColor="var(--g-yin)">
          {[
            { n: '贵州茅台', rule: 'price<1200', cur: '1684',    pct: '─' },
            { n: '宁德时代', rule: 'pct>=5%',   cur: '+2.21%', pct: '◐', hot: true },
            { n: '比亚迪',   rule: 'price>300', cur: '281.40', pct: '◑' },
          ].map((a, i) => (
            <div key={i} style={{ padding: '4px 18px', fontSize: 11, display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ color: a.hot ? 'var(--g-yin)' : 'var(--g-ink-3)', width: 8 }}>{a.pct}</span>
              <span style={{ width: 60, color: 'var(--g-ink)' }}>{a.n}</span>
              <span style={{ flex: 1, color: 'var(--g-ink-3)', fontSize: 10 }}>{a.rule}</span>
              <span style={{ color: 'var(--g-ink-2)' }}>{a.cur}</span>
            </div>
          ))}
        </GeekRail>

        <GeekRail label="history.today">
          {[
            { t: '看下宁德时代怎么样', s: 'running…', active: true },
            { t: '今天主力在买什么', s: '04:18 ok' },
            { t: 'CPO 板块还能不能追', s: '02:31 ok' },
            { t: '茅台跌破1200提醒我', s: '08:14 alert.set' },
          ].map((h, i) => (
            <div key={i} style={{
              padding: '5px 18px', fontSize: 11, cursor: 'pointer',
              borderLeft: h.active ? '2px solid var(--g-amber)' : '2px solid transparent',
              background: h.active ? 'rgba(217,162,92,0.06)' : 'transparent',
            }}>
              <div style={{ color: h.active ? 'var(--g-ink)' : 'var(--g-ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {h.active && '▶ '}{h.t}
              </div>
              <div style={{ color: 'var(--g-ink-3)', fontSize: 9, marginTop: 1 }}>{h.s}</div>
            </div>
          ))}
        </GeekRail>

        <div style={{ flex: 1 }} />

        {/* footer */}
        <div style={{ padding: '10px 18px', borderTop: '1px solid var(--g-line)', display: 'flex', alignItems: 'center', gap: 10, fontSize: 10 }}>
          <span style={{ color: 'var(--g-amber)' }}>⌘K</span>
          <span style={{ color: 'var(--g-ink-2)', flex: 1 }}>tools · 26</span>
          <span style={{ color: 'var(--g-ink-3)' }}>cmd palette</span>
        </div>
      </aside>

      {/* ═════ 中央 ═════ */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>

        {/* 顶栏 */}
        <header style={{ padding: '12px 28px', borderBottom: '1px solid var(--g-line)', display: 'flex', alignItems: 'center', gap: 18, flexShrink: 0, background: 'var(--g-surface)' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, color: 'var(--g-ink)', display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span style={{ color: 'var(--g-amber)' }}>▸</span>
              <span>看下宁德时代怎么样</span>
              <span style={{ color: 'var(--g-ink-3)', fontSize: 10 }}>// task#0428</span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--g-ink-3)', marginTop: 3 }}>
              symbol=300750.SZ · tool=stock_brief · steps=5 · elapsed=38.2s
            </div>
          </div>

          {/* mini ticker */}
          <div style={{ display: 'flex', gap: 16, fontSize: 11 }}>
            {[{n:'SH',v:'3287.42',d:'+0.46'},{n:'SZ',v:'10524.11',d:'+0.82'},{n:'CYB',v:'2114.08',d:'+1.12'}].map((x,i) => (
              <span key={i}>
                <span style={{ color: 'var(--g-ink-3)' }}>{x.n}</span>{' '}
                <span style={{ color: 'var(--g-ink)' }}>{x.v}</span>{' '}
                <span style={{ color: x.d.startsWith('-') ? 'var(--g-down)' : 'var(--g-up)' }}>{x.d}%</span>
              </span>
            ))}
          </div>
          <span style={{ color: 'var(--g-line-2)' }}>│</span>
          <div style={{ fontSize: 10, color: 'var(--g-ink-2)' }}>
            <span style={{ color: 'var(--g-up)' }}>●</span> trading · 14:17 <span style={{ color: 'var(--g-ink-3)' }}>· close in 00:42:17</span>
          </div>
        </header>

        {/* transcript */}
        <div style={{ flex: 1, overflow: 'hidden', padding: '20px 48px 16px', display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* user */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
            <span style={{ color: 'var(--g-amber)', fontSize: 13 }}>❯</span>
            <span style={{ fontSize: 14, color: 'var(--g-ink)' }}>看下宁德时代怎么样</span>
            <span style={{ color: 'var(--g-ink-3)', fontSize: 10, marginLeft: 'auto' }}>14:17:42</span>
          </div>

          {/* tool chain */}
          <GeekToolChain />

          {/* brief card */}
          <GeekBriefCard />

          {/* LLM summary */}
          <GeekSummary />
        </div>

        {/* composer */}
        <div style={{ padding: '12px 48px 8px', borderTop: '1px solid var(--g-line)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, padding: '10px 14px', border: '1px solid var(--g-line-2)', background: 'var(--g-surface)' }}>
            <span style={{ color: 'var(--g-amber)' }}>❯</span>
            <span style={{ color: 'var(--g-ink-3)', flex: 1 }}>continue, or /command…</span>
            <span style={{ fontSize: 10, color: 'var(--g-ink-3)' }}>[Tab] complete · [↵] send · [ESC] cancel</span>
          </div>
          {/* slash hint row */}
          <div style={{ marginTop: 6, display: 'flex', gap: 14, fontSize: 10, color: 'var(--g-ink-3)' }}>
            {['/help','/tools','/mode default','/model qwen3.5-plus','/watch on 5m','/save','@300750','⌗ board'].map((x,i) => (
              <span key={i} style={{ cursor: 'pointer' }}><span style={{ color: 'var(--g-amber)' }}>{x.charAt(0)}</span>{x.slice(1)}</span>
            ))}
          </div>
        </div>

        {/* status bar */}
        <footer style={{ padding: '6px 28px 8px', borderTop: '1px solid var(--g-line)', display: 'flex', alignItems: 'center', gap: 14, fontSize: 10, color: 'var(--g-ink-2)', background: 'var(--g-surface)', flexShrink: 0 }}>
          <span style={{ padding: '2px 7px', border: '1px solid var(--g-down)', color: 'var(--g-down)' }}>[default]</span>
          <span><span style={{ color: 'var(--g-ink-3)' }}>model</span> qwen3.5-plus</span>
          <span style={{ color: 'var(--g-line-2)' }}>│</span>
          <span><span style={{ color: 'var(--g-ink-3)' }}>tok</span> 2.3k <span style={{ color: 'var(--g-ink-3)' }}>(↑1800 ↓500 · 4 calls)</span></span>
          <span style={{ color: 'var(--g-line-2)' }}>│</span>
          <span><span style={{ color: 'var(--g-ink-3)' }}>watch</span> 5m <span style={{ color: 'var(--g-up)' }}>· trading</span></span>
          <span style={{ color: 'var(--g-line-2)' }}>│</span>
          <span style={{ color: 'var(--g-ink-3)' }}>auto-approved:</span>
          <span style={{ padding: '1px 5px', border: '1px solid var(--g-line-2)' }}>news_query</span>
          <span style={{ padding: '1px 5px', border: '1px solid var(--g-line-2)' }}>quote_lookup</span>
          <span style={{ flex: 1 }} />
          <span style={{ color: 'var(--g-ink-3)' }}>~/.financial-analyst/buddy.yaml</span>
        </footer>
      </main>

      {/* ═════ 右栏 ═════ */}
      <GeekRightRail />
    </div>
  );
};

// ───── Rail section ─────
const GeekRail = ({ label, count, countColor, children }) => (
  <div style={{ paddingTop: 8, paddingBottom: 8, borderBottom: '1px solid var(--g-line)' }}>
    <div style={{ display: 'flex', alignItems: 'center', padding: '4px 18px 6px', fontSize: 10, color: 'var(--g-ink-3)' }}>
      <span style={{ color: 'var(--g-amber)' }}>┌─</span>
      <span style={{ marginLeft: 4, color: 'var(--g-ink-2)', letterSpacing: '0.05em' }}>{label}</span>
      <span style={{ flex: 1, marginLeft: 8, color: 'var(--g-line-2)', overflow: 'hidden' }}>─────────────────────</span>
      {count && <span style={{ color: countColor || 'var(--g-ink-3)' }}>{count}</span>}
    </div>
    {children}
  </div>
);

// ───── Tool chain ─────
const GeekToolChain = () => {
  const tools = [
    { i: '01', name: 'realtime_quote',  args: 'symbol="300750"', t: '0.4s', status: 'ok', result: '325.10 +2.21% · 量比 1.42 · 换手 2.18%' },
    { i: '02', name: 'ths_fund_flow',   args: 'target=stock, symbol="300750"', t: '2.8s', status: 'ok', result: '主力净 +4.82亿 · 大单 +3.1 · 中单 +1.7' },
    { i: '03', name: 'news_query',      args: 'kw="宁德时代", days=7', t: '0.2s', status: 'ok', result: '14 · 巨潮×2 雪球×5 东财×7' },
    { i: '04', name: 'chain_for',       args: 'symbol="300750"', t: '0.1s', status: 'ok', result: '上游 6 · 同行 5 · 下游 8' },
    { i: '05', name: 'stocks_show',     args: 'symbol="300750", limit=3', t: '...', status: 'run', result: 'querying SQLite…' },
  ];
  return (
    <div style={{ padding: '10px 14px', border: '1px solid var(--g-line-2)', background: 'var(--g-surface)', fontSize: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingBottom: 8, borderBottom: '1px dashed var(--g-line-2)', marginBottom: 8 }}>
        <span style={{ color: 'var(--g-cyan)' }}>▶</span>
        <span style={{ color: 'var(--g-ink)' }}>research.chain</span>
        <span style={{ color: 'var(--g-ink-3)', fontSize: 10 }}>stock_brief · 5 steps · 4/5 done · 4.2s</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: 'var(--g-ink-3)', fontSize: 10 }}>[Esc] cancel · [⇧+E] expand</span>
      </div>
      {tools.map((t, i) => (
        <div key={i} style={{ display: 'flex', gap: 10, padding: '3px 0', alignItems: 'baseline', fontSize: 11 }}>
          <span style={{ color: 'var(--g-ink-3)', width: 22 }}>{t.i}</span>
          <span style={{ color: t.status === 'run' ? 'var(--g-amber-d)' : 'var(--g-down)', width: 14 }}>
            {t.status === 'run' ? '⠋' : '✓'}
          </span>
          <span style={{ color: 'var(--g-amber)', width: 150 }}>{t.name}</span>
          <span style={{ color: 'var(--g-ink-3)', flex: '0 1 240px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>({t.args})</span>
          <span style={{ color: 'var(--g-ink-3)', width: 40, textAlign: 'right' }}>{t.t}</span>
          <span style={{ color: t.status === 'run' ? 'var(--g-ink-3)' : 'var(--g-ink)', flex: 1, fontStyle: t.status === 'run' ? 'italic' : 'normal' }}>
            → {t.result}
          </span>
        </div>
      ))}
    </div>
  );
};

// ───── Brief card ─────
const GeekBriefCard = () => (
  <div style={{ border: '1px solid var(--g-amber)', background: 'var(--g-surface)' }}>
    {/* header */}
    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--g-line-2)', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{ fontSize: 11, color: 'var(--g-amber)', letterSpacing: '0.15em' }}>[STOCK_BRIEF]</span>
          <span style={{ fontSize: 18, color: 'var(--g-ink)', fontWeight: 600 }}>宁德时代</span>
          <span style={{ fontSize: 11, color: 'var(--g-ink-3)' }}>300750.SZ · 深主板 · 电力设备/电池</span>
        </div>
        <div style={{ fontSize: 10, color: 'var(--g-ink-3)', marginTop: 4 }}>market_cap=14,206亿 · float_cap=8,142亿 · shares_out=43.7亿</div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div style={{ fontSize: 22, color: 'var(--g-up)', fontWeight: 600 }}>325.10</div>
        <div style={{ fontSize: 11, color: 'var(--g-up)' }}>+7.04 (+2.21%)</div>
      </div>
    </div>

    {/* 4-grid */}
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', fontSize: 11 }}>
      {/* 行情 */}
      <GeekBriefRegion title="quote.metrics" cite="01">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
          {[
            { l: 'vol_ratio', v: '1.42' }, { l: 'turn_rate', v: '2.18%' }, { l: 'amplitude', v: '3.84%' },
            { l: 'PE_TTM', v: '21.4' }, { l: 'PB', v: '4.6' }, { l: 'ROE', v: '24.6%' },
          ].map((m, i) => (
            <div key={i}>
              <div style={{ color: 'var(--g-ink-3)', fontSize: 9 }}>{m.l}</div>
              <div style={{ color: 'var(--g-ink)', fontSize: 13 }}>{m.v}</div>
            </div>
          ))}
        </div>
      </GeekBriefRegion>

      <GeekBriefRegion title="fund_flow.today" cite="02" borderL>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 6 }}>
          <span style={{ fontSize: 18, color: 'var(--g-up)' }}>+4.82</span>
          <span style={{ fontSize: 10, color: 'var(--g-ink-2)' }}>亿 net_in</span>
          <span style={{ marginLeft: 'auto', color: 'var(--g-ink-3)', fontSize: 9 }}>prev +1.9</span>
        </div>
        {[
          { l: 'XL', v: '+2.1', pct: 70, up: true },
          { l: 'L',  v: '+1.0', pct: 33, up: true },
          { l: 'M',  v: '+1.7', pct: 56, up: true },
          { l: 'S',  v: '-0.4', pct: 13, up: false },
        ].map((b, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3, fontSize: 10 }}>
            <span style={{ color: 'var(--g-ink-3)', width: 20 }}>{b.l}</span>
            <div style={{ flex: 1, height: 3, background: 'var(--g-line)' }}>
              <div style={{ width: `${b.pct}%`, height: '100%', background: b.up ? 'var(--g-up)' : 'var(--g-down)' }} />
            </div>
            <span style={{ color: b.up ? 'var(--g-up)' : 'var(--g-down)', width: 36, textAlign: 'right' }}>{b.v}亿</span>
          </div>
        ))}
      </GeekBriefRegion>

      <GeekBriefRegion title="news.recent" sub="14 in 7d" cite="03" borderT>
        {[
          { t: '签订 19 GWh 海外储能订单', s: 'cninfo·11-04', tone: 'up' },
          { t: '中信 BUY 维持 · TP=360', s: 'citics·11-12', tone: 'up' },
          { t: '10 月电池装车 +51% YoY', s: 'caam·11-12', tone: 'up' },
        ].map((n, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '3px 0', borderTop: i ? '1px dashed var(--g-line)' : 'none' }}>
            <span style={{ color: n.tone === 'up' ? 'var(--g-up)' : 'var(--g-down)' }}>•</span>
            <span style={{ flex: 1, color: 'var(--g-ink-2)', fontSize: 11 }}>{n.t}</span>
            <span style={{ color: 'var(--g-ink-3)', fontSize: 9 }}>{n.s}</span>
          </div>
        ))}
      </GeekBriefRegion>

      <GeekBriefRegion title="chain.peers" cite="04" borderL borderT>
        <div style={{ color: 'var(--g-ink-3)', fontSize: 9, marginBottom: 4 }}>peer.gross_margin</div>
        {[
          { n: '300750.SZ 宁德时代', v: '28.4', pct: 95, focus: true },
          { n: '300014.SZ 亿纬锂能', v: '17.2', pct: 58 },
          { n: '002074.SZ 国轩高科', v: '14.6', pct: 49 },
          { n: '300919.SZ 中创新航', v: '11.8', pct: 39 },
        ].map((r, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3, fontSize: 10 }}>
            <span style={{ color: r.focus ? 'var(--g-amber)' : 'var(--g-ink-2)', width: 130 }}>{r.n}</span>
            <div style={{ flex: 1, height: 3, background: 'var(--g-line)' }}>
              <div style={{ width: `${r.pct}%`, height: '100%', background: r.focus ? 'var(--g-amber)' : 'var(--g-ink-3)' }} />
            </div>
            <span style={{ color: 'var(--g-ink)', width: 32, textAlign: 'right' }}>{r.v}%</span>
          </div>
        ))}
        <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px dashed var(--g-line)', display: 'flex', alignItems: 'center', gap: 8, fontSize: 10 }}>
          <span style={{ color: 'var(--g-ink-3)' }}>xueqiu.sentiment</span>
          <div style={{ flex: 1, height: 3, background: 'var(--g-line)' }}>
            <div style={{ width: '68%', height: '100%', background: 'var(--g-up)' }} />
          </div>
          <span style={{ color: 'var(--g-up)' }}>bull 68%</span>
        </div>
      </GeekBriefRegion>
    </div>

    {/* action bar */}
    <div style={{ padding: '7px 16px', display: 'flex', gap: 14, fontSize: 10, color: 'var(--g-ink-2)', borderTop: '1px solid var(--g-line-2)' }}>
      <span style={{ cursor: 'pointer' }}>[s] save.md</span>
      <span style={{ cursor: 'pointer' }}>[+] watchlist</span>
      <span style={{ cursor: 'pointer' }}>[a] add.alert</span>
      <span style={{ cursor: 'pointer', color: 'var(--g-amber)' }}>[r] run_report ▸ 6m·needs confirm</span>
      <span style={{ flex: 1 }} />
      <span style={{ color: 'var(--g-ink-3)' }}>refs: 01·02·03·04 · sources=7</span>
    </div>
  </div>
);

const GeekBriefRegion = ({ title, sub, cite, children, borderL, borderT }) => (
  <div style={{ padding: '10px 14px', borderLeft: borderL ? '1px solid var(--g-line-2)' : 'none', borderTop: borderT ? '1px solid var(--g-line-2)' : 'none' }}>
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 6 }}>
      <span style={{ color: 'var(--g-amber)', fontSize: 11 }}>▸</span>
      <span style={{ color: 'var(--g-ink), fontSize: 11', fontSize: 11 }}>{title}</span>
      {sub && <span style={{ color: 'var(--g-ink-3)', fontSize: 9 }}>{sub}</span>}
      <span style={{ flex: 1 }} />
      {cite && <span style={{ color: 'var(--g-amber)', fontSize: 9, padding: '0 4px', border: '1px solid var(--g-amber)' }}>{cite}</span>}
    </div>
    {children}
  </div>
);

// ───── LLM summary ─────
const GeekSummary = () => (
  <div style={{ display: 'flex', gap: 12, fontSize: 13 }}>
    <span style={{ color: 'var(--g-cyan)', flexShrink: 0 }}>◇</span>
    <div style={{ flex: 1, lineHeight: 1.85, color: 'var(--g-ink)' }}>
      宁德 Q3 利润 <span style={{ color: 'var(--g-up)' }}>+25.9% YoY</span> 超预期，毛利率回升至 28.4%<GCite n="01"/>；今日主力净 <span style={{ color: 'var(--g-up)' }}>+4.82亿</span>，连续 3 日加仓<GCite n="02"/>。
      催化：<span style={{ color: 'var(--g-amber)' }}>19 GWh 海外储能订单</span> + 中信 TP=360<GCite n="03"/>；
      同行毛利率仍处第一梯队<GCite n="04"/>。短线 sentiment=bull，
      建议 <span style={{ background: 'rgba(217,162,92,0.18)', padding: '0 4px' }}>分批跟进 · 关注 330 压力</span>。
      <span style={{ display: 'inline-block', marginLeft: 6, padding: '1px 5px', background: 'var(--g-line)', color: 'var(--g-ink-3)', fontSize: 10 }}>streaming…</span>
    </div>
  </div>
);

const GCite = ({ n }) => (
  <sup style={{
    display: 'inline-block', padding: '0 4px', marginLeft: 2,
    background: 'var(--g-amber)', color: 'var(--g-bg)',
    fontSize: 8, fontWeight: 700, cursor: 'pointer', verticalAlign: 2
  }}>{n}</sup>
);

// ───── 右栏 ─────
const GeekRightRail = () => (
  <aside style={{ width: 316, borderLeft: '1px solid var(--g-line)', display: 'flex', flexDirection: 'column', flexShrink: 0, background: 'var(--g-surface)', fontSize: 11 }}>

    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--g-line)', display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ color: 'var(--g-yin)' }}>●</span>
      <span style={{ color: 'var(--g-ink)', fontWeight: 600 }}>watch.daemon</span>
      <span style={{ color: 'var(--g-ink-3)', fontSize: 10 }}>every 5m</span>
      <span style={{ flex: 1 }} />
      <span style={{ color: 'var(--g-amber)', cursor: 'pointer', fontSize: 10 }}>/watch ▾</span>
    </div>

    {/* fired alert */}
    <div style={{ padding: '10px 16px' }}>
      <div style={{ color: 'var(--g-ink-3)', fontSize: 9, letterSpacing: '0.15em', marginBottom: 6 }}>// FIRED 13:54:08</div>
      <div style={{ border: '1px solid var(--g-yin)', padding: '10px 12px', background: 'rgba(201,63,63,0.08)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: 'var(--g-yin)' }}>▲</span>
          <span style={{ color: 'var(--g-ink)' }}>中际旭创 <span style={{ color: 'var(--g-ink-3)' }}>300308</span></span>
          <span style={{ marginLeft: 'auto', color: 'var(--g-up)', fontWeight: 600 }}>+4.12%</span>
        </div>
        <div style={{ color: 'var(--g-ink-2)', fontSize: 11, marginTop: 6 }}>rule: <span style={{ color: 'var(--g-amber)' }}>pct_above 4</span></div>
        <div style={{ color: 'var(--g-ink-3)', fontSize: 10, marginTop: 4 }}>price=142.55 vol_ratio=2.1 13:54</div>
        <div style={{ display: 'flex', gap: 6, marginTop: 8, fontSize: 10 }}>
          <span style={{ padding: '2px 6px', border: '1px solid var(--g-line-2)', cursor: 'pointer' }}>[a] ask →</span>
          <span style={{ padding: '2px 6px', border: '1px solid var(--g-line-2)', cursor: 'pointer' }}>[p] pause</span>
          <span style={{ padding: '2px 6px', border: '1px solid var(--g-line-2)', cursor: 'pointer' }}>[d] drop</span>
        </div>
      </div>
    </div>

    {/* active rules */}
    <div style={{ padding: '10px 16px', borderTop: '1px solid var(--g-line)' }}>
      <div style={{ color: 'var(--g-ink-3)', fontSize: 9, letterSpacing: '0.15em', marginBottom: 8 }}>// ACTIVE 3</div>
      {[
        { n: '贵州茅台', rule: 'price<1200', cur: '1684',   pct: 28, far: '+40.3%' },
        { n: '宁德时代', rule: 'pct≥5%',     cur: '+2.21%', pct: 44, far: 'rem 2.79pct' },
        { n: '比亚迪',   rule: 'price>300',  cur: '281.40', pct: 92, far: 'rem 6.62%' },
      ].map((a, i) => (
        <div key={i} style={{ padding: '8px 0', borderTop: i ? '1px dashed var(--g-line)' : 'none' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ color: 'var(--g-ink)' }}>{a.n}</span>
            <span style={{ color: 'var(--g-ink-3)', fontSize: 10 }}>{a.rule}</span>
            <span style={{ marginLeft: 'auto', color: 'var(--g-ink-2)', fontSize: 11 }}>{a.cur}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
            <div style={{ flex: 1, height: 2, background: 'var(--g-line)' }}>
              <div style={{ width: `${a.pct}%`, height: '100%', background: 'var(--g-amber)' }} />
            </div>
            <span style={{ color: 'var(--g-ink-3)', fontSize: 9 }}>{a.far}</span>
          </div>
        </div>
      ))}
    </div>

    {/* citations */}
    <div style={{ padding: '10px 16px', borderTop: '1px solid var(--g-line)' }}>
      <div style={{ color: 'var(--g-ink-3)', fontSize: 9, letterSpacing: '0.15em', marginBottom: 8 }}>// REFS · 4</div>
      {[
        { n: '01', src: 'realtime_quote', t: 'ths · 14:17',     d: '325.10 +2.21%' },
        { n: '02', src: 'ths_fund_flow',  t: 'ths · 14:17',     d: 'net_in 4.82亿' },
        { n: '03', src: 'news_query',     t: 'sqlite · 7d',     d: '14 docs · cninfo/citics/caam' },
        { n: '04', src: 'chain_for',      t: 'qlib.industry',   d: 'peers=5 up=6 down=8' },
      ].map((c, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, padding: '6px 0', borderTop: i ? '1px dashed var(--g-line)' : 'none', fontSize: 10 }}>
          <span style={{ color: 'var(--g-amber)', padding: '0 4px', background: 'rgba(217,162,92,0.1)', flexShrink: 0, fontSize: 9 }}>{c.n}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: 'var(--g-amber)' }}>{c.src}</div>
            <div style={{ color: 'var(--g-ink-2)', marginTop: 1 }}>{c.d}</div>
            <div style={{ color: 'var(--g-ink-3)', fontSize: 9, marginTop: 1 }}>{c.t}</div>
          </div>
        </div>
      ))}
    </div>

    <div style={{ flex: 1 }} />

    <div style={{ padding: '8px 16px', borderTop: '1px solid var(--g-line)', display: 'flex', alignItems: 'center', gap: 6, fontSize: 10 }}>
      <span style={{ color: 'var(--g-down)' }}>●</span>
      <span style={{ color: 'var(--g-ink-2)' }}>all sources ≤ 4m</span>
      <span style={{ flex: 1 }} />
      <span style={{ color: 'var(--g-amber)', cursor: 'pointer' }}>[r] refresh</span>
    </div>
  </aside>
);

window.GeekStream = GeekStream;
