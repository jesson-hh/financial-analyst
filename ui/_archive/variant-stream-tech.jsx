// 科技风 · 甲 · 流 — 深空玻璃 HUD
// 1440 x 900
// 灵感：交易终端 + 飞控 HUD + Vision Pro 玻璃面板
// 深空蓝底 · 半透明玻璃 · 青色高亮 · 细线几何 · 微辉光

const TechStream = () => {
  const W = 1440, H = 1300;

  return (
    <div className="tech-scope" style={{
      width: W, height: H, display: 'flex',
      fontFamily: '"Space Grotesk", "Inter", system-ui, sans-serif',
      color: 'var(--t-ink)',
      background: 'radial-gradient(120% 100% at 30% 0%, #0e1830 0%, var(--t-bg) 55%), radial-gradient(80% 70% at 100% 100%, #1a0c2a 0%, transparent 60%), var(--t-bg)',
      position: 'relative', overflow: 'hidden'
    }}>
      {/* 全局背景网格 */}
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 0.5,
        backgroundImage:
          'linear-gradient(rgba(140,170,210,0.04) 1px, transparent 1px),' +
          'linear-gradient(90deg, rgba(140,170,210,0.04) 1px, transparent 1px)',
        backgroundSize: '32px 32px',
      }} />

      {/* 左栏 */}
      <TechLeftRail />

      {/* 中央 */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, position: 'relative' }}>
        <TechTopHud />
        <TechTranscript />
        <TechComposer />
      </main>

      {/* 右栏 */}
      <TechRightRail />
    </div>
  );
};

// ───── 左栏 ─────
const TechLeftRail = () => (
  <aside style={{
    width: 244, flexShrink: 0, padding: '20px 16px', display: 'flex', flexDirection: 'column',
    borderRight: '1px solid var(--t-line)', background: 'rgba(8,12,22,0.5)', backdropFilter: 'blur(20px)',
    fontFamily: '"Inter", system-ui, sans-serif',
    position: 'relative', zIndex: 1
  }}>
    {/* 品牌 */}
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
      <div style={{
        width: 34, height: 34, position: 'relative',
        background: 'linear-gradient(135deg, var(--t-cyan), var(--t-magenta))',
        clipPath: 'polygon(0 0, 100% 0, 100% 70%, 70% 100%, 0 100%)',
      }}>
        <div style={{ position: 'absolute', inset: 2, background: 'var(--t-bg-2)', clipPath: 'inherit', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: '"Space Grotesk"', fontSize: 14, color: 'var(--t-cyan)', fontWeight: 600 }}>觀</div>
      </div>
      <div>
        <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.08em' }}>GUANLAN</div>
        <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 9, color: 'var(--t-ink-3)', letterSpacing: '0.2em' }}>// A-SHARE.AI</div>
      </div>
    </div>

    {/* new chat */}
    <button style={{
      padding: '10px 12px', background: 'linear-gradient(180deg, rgba(77,213,255,0.18), rgba(77,213,255,0.06))',
      border: '1px solid var(--t-cyan)', color: 'var(--t-cyan-d)', fontFamily: 'inherit', fontSize: 12,
      letterSpacing: '0.06em', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      marginBottom: 18, fontWeight: 500,
      boxShadow: '0 0 16px rgba(77,213,255,0.18)'
    }}>
      <span>＋ NEW SESSION</span>
      <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, opacity: 0.6 }}>⌘N</span>
    </button>

    {/* watchlist */}
    <TechSection label="WATCHLIST" count="06">
      {[
        { n: '宁德时代', c: '300750', p: '325.10', d: '+2.21', up: true, spark: [240,252,261,270,275,280,292,305,312,325] },
        { n: '贵州茅台', c: '600519', p: '1,684', d: '-0.42', up: false, spark: [1720,1690,1700,1685,1690,1684] },
        { n: '比亚迪',   c: '002594', p: '281.40', d: '+1.68', up: true, spark: [262,265,268,272,275,278,281] },
        { n: '中际旭创', c: '300308', p: '142.55', d: '+4.12', up: true, spark: [125,128,135,140,142], hot: true },
      ].map((r, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderTop: i ? '1px solid var(--t-line)' : 'none', cursor: 'pointer' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12, color: 'var(--t-ink)' }}>{r.n} {r.hot && <span style={{ color: 'var(--t-amber)', fontSize: 9 }}>◆</span>}</div>
            <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)' }}>{r.c}</div>
          </div>
          <TechSpark data={r.spark} up={r.up} />
          <div style={{ textAlign: 'right', minWidth: 52 }}>
            <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 11, color: 'var(--t-ink)' }}>{r.p}</div>
            <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: r.up ? 'var(--t-up)' : 'var(--t-down)' }}>{r.d}%</div>
          </div>
        </div>
      ))}
    </TechSection>

    {/* alerts */}
    <TechSection label="ACTIVE.ALERTS" count="03" countColor="var(--t-amber)">
      {[
        { n: '中际旭创', rule: 'PCT_ABV 4%', cur: '+4.12%', fire: true },
        { n: '贵州茅台', rule: 'PRC_BLW 1,200', cur: '1,684' },
        { n: '宁德时代', rule: 'PCT_ABV 5%', cur: '+2.21%' },
      ].map((a, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderTop: i ? '1px solid var(--t-line)' : 'none' }}>
          <span style={{
            width: 6, height: 6, background: a.fire ? 'var(--t-amber)' : 'var(--t-ink-3)',
            boxShadow: a.fire ? '0 0 6px var(--t-amber)' : 'none', flexShrink: 0
          }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 11.5, color: 'var(--t-ink)' }}>{a.n}</div>
            <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)' }}>{a.rule}</div>
          </div>
          <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-2)' }}>{a.cur}</div>
        </div>
      ))}
    </TechSection>

    {/* history */}
    <TechSection label="TODAY">
      {[
        { t: '看下宁德时代怎么样', s: '> running 38s', active: true },
        { t: '今天主力在买什么',  s: '04:18 · complete' },
        { t: 'CPO 板块还能不能追', s: '02:31 · complete' },
        { t: '茅台跌破 1200 提醒我', s: '08:14 · alert set' },
      ].map((h, i) => (
        <div key={i} style={{
          padding: '7px 8px', cursor: 'pointer', marginLeft: -8, marginRight: -8,
          borderLeft: h.active ? '2px solid var(--t-cyan)' : '2px solid transparent',
          background: h.active ? 'linear-gradient(90deg, rgba(77,213,255,0.08), transparent)' : 'transparent',
        }}>
          <div style={{ fontSize: 11.5, color: h.active ? 'var(--t-ink)' : 'var(--t-ink-2)' }}>{h.t}</div>
          <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: h.active ? 'var(--t-cyan)' : 'var(--t-ink-3)', marginTop: 2 }}>{h.s}</div>
        </div>
      ))}
    </TechSection>

    <div style={{ flex: 1 }} />

    {/* tools palette */}
    <div style={{
      padding: '10px 12px', border: '1px solid var(--t-line)', display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
      background: 'var(--t-surface)'
    }}>
      <span style={{ width: 22, height: 22, border: '1px solid var(--t-cyan)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--t-cyan)', fontFamily: '"JetBrains Mono"', fontSize: 11 }}>⌘</span>
      <span style={{ fontSize: 11, color: 'var(--t-ink-2)', flex: 1 }}>tools · 26</span>
      <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)' }}>⌘K</span>
    </div>
  </aside>
);

const TechSection = ({ label, count, countColor, children }) => (
  <div style={{ marginBottom: 18 }}>
    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6, fontFamily: '"JetBrains Mono"', fontSize: 9, letterSpacing: '0.2em' }}>
      <span style={{ color: 'var(--t-ink-3)' }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: 'var(--t-line)', marginLeft: 8, marginRight: 8 }} />
      {count && <span style={{ color: countColor || 'var(--t-ink-3)' }}>{count}</span>}
    </div>
    {children}
  </div>
);

const TechSpark = ({ data, up }) => {
  const max = Math.max(...data), min = Math.min(...data);
  const w = 44, h = 14;
  const dx = w / (data.length - 1);
  const y = (v) => h - ((v - min) / (max - min || 1)) * h;
  const points = data.map((v, i) => `${i * dx},${y(v)}`).join(' ');
  return (
    <svg width={w} height={h}>
      <polyline points={points} fill="none" stroke={up ? 'var(--t-up)' : 'var(--t-down)'} strokeWidth="1.2" />
    </svg>
  );
};

// ───── 顶部 HUD ─────
const TechTopHud = () => (
  <header style={{
    padding: '14px 28px 12px', borderBottom: '1px solid var(--t-line)', display: 'flex', alignItems: 'center', gap: 14, flexShrink: 0, flexWrap: 'nowrap',
    background: 'rgba(8,12,22,0.4)', backdropFilter: 'blur(20px)', position: 'relative', zIndex: 1
  }}>
    {/* corner brackets */}
    <div style={{ position: 'absolute', top: 10, left: 16, width: 8, height: 8, borderTop: '1px solid var(--t-cyan)', borderLeft: '1px solid var(--t-cyan)' }} />
    <div style={{ position: 'absolute', top: 10, right: 16, width: 8, height: 8, borderTop: '1px solid var(--t-cyan)', borderRight: '1px solid var(--t-cyan)' }} />

    <div style={{ flex: '1 1 0', minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, whiteSpace: 'nowrap', overflow: 'hidden' }}>
        <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-cyan)', letterSpacing: '0.15em', flexShrink: 0 }}>TASK_0428</span>
        <span style={{ fontSize: 15, color: 'var(--t-ink)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis' }}>看下宁德时代怎么样</span>
      </div>
      <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-3)', marginTop: 3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        SYM=300750.SZ · stock_brief · 5步 · 38.2s · <span style={{ color: 'var(--t-amber)' }}>STREAMING</span> ·
        <span style={{ color: 'var(--t-down)', marginLeft: 6 }}>DEFAULT</span> ·
        <span style={{ color: 'var(--t-ink-2)', marginLeft: 6 }}>qwen3.5-plus</span> ·
        <span style={{ color: 'var(--t-ink-2)', marginLeft: 6 }}>2.3k TOK</span>
      </div>
    </div>

    {/* index meters */}
    <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
      {[
        { n: 'SH',  v: '3287.42', d: '+0.46', up: true },
        { n: 'SZ',  v: '10524.11', d: '+0.82', up: true },
        { n: 'CYB', v: '2114.08', d: '+1.12', up: true },
      ].map((x, i) => (
        <div key={i} style={{ padding: '4px 8px', border: '1px solid var(--t-line)', background: 'var(--t-surface)' }}>
          <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)', letterSpacing: '0.1em' }}>{x.n}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
            <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 11, color: 'var(--t-ink)' }}>{x.v}</span>
            <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: x.up ? 'var(--t-up)' : 'var(--t-down)' }}>{x.d}%</span>
          </div>
        </div>
      ))}
    </div>

    {/* trading status */}
    <div style={{ flexShrink: 0, fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-up)', display: 'flex', alignItems: 'center', gap: 5, padding: '4px 10px', border: '1px solid var(--t-up)', background: 'rgba(255,91,110,0.08)' }}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--t-up)', boxShadow: '0 0 6px var(--t-up)' }} />
      <span>TRADING 14:17</span>
      <span style={{ color: 'var(--t-ink-3)' }}>-00:42:17</span>
    </div>
  </header>
);

const Chip = ({ label, color, dot, mono }) => (
  <span style={{
    display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 8px',
    border: `1px solid ${color || 'var(--t-line-2)'}`,
    color: color || 'var(--t-ink-2)',
    fontFamily: mono ? '"JetBrains Mono"' : '"Inter"', fontSize: 10, letterSpacing: '0.06em',
    background: 'var(--t-surface)'
  }}>
    {dot && <span style={{ width: 5, height: 5, borderRadius: '50%', background: color || 'currentColor' }} />}
    {label}
  </span>
);

// ───── transcript ─────
const TechTranscript = () => (
  <div style={{ flex: 1, overflow: 'hidden', padding: '24px 36px', display: 'flex', flexDirection: 'column', gap: 16, position: 'relative', zIndex: 1 }}>
    {/* user */}
    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <div style={{
        maxWidth: '70%', padding: '11px 16px',
        background: 'linear-gradient(135deg, rgba(77,213,255,0.16), rgba(77,213,255,0.06))',
        border: '1px solid var(--t-line-2)', color: 'var(--t-ink)', fontSize: 14, lineHeight: 1.6,
        position: 'relative'
      }}>
        <div style={{ position: 'absolute', top: -1, right: -1, width: 8, height: 8, borderTop: '1px solid var(--t-cyan)', borderRight: '1px solid var(--t-cyan)' }} />
        看下宁德时代怎么样
      </div>
    </div>

    {/* tool chain */}
    <TechToolChain />

    {/* brief card */}
    <TechBriefCard />

    {/* summary */}
    <TechSummary />
  </div>
);

const TechAvatar = () => (
  <div style={{
    width: 30, height: 30, flexShrink: 0, position: 'relative',
    background: 'linear-gradient(135deg, var(--t-cyan), var(--t-magenta))',
    clipPath: 'polygon(0 0, 100% 0, 100% 70%, 70% 100%, 0 100%)',
  }}>
    <div style={{
      position: 'absolute', inset: 1.5, background: 'var(--t-bg-2)', clipPath: 'inherit',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: '"Space Grotesk"', fontSize: 12, color: 'var(--t-cyan)', fontWeight: 600
    }}>觀</div>
  </div>
);

const TechToolChain = () => {
  const tools = [
    { i: '01', name: 'realtime_quote',  cn: '实时行情', t: '0.4s', status: 'done', result: '325.10 · +2.21% · vol×1.42' },
    { i: '02', name: 'ths_fund_flow',   cn: '主力资金', t: '2.8s', status: 'done', result: 'net_in +4.82亿 · L+3.1 · M+1.7' },
    { i: '03', name: 'news_query',      cn: '新闻检索', t: '0.2s', status: 'done', result: '14 docs · cninfo×2 xq×5 east×7' },
    { i: '04', name: 'chain_for',       cn: '产业链',   t: '0.1s', status: 'done', result: 'up=6 · peer=5 · down=8' },
    { i: '05', name: 'stocks_show',     cn: '研究档案', t: '...',  status: 'run', result: 'querying sqlite…' },
  ];
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <TechAvatar />
      <div style={{ flex: 1, minWidth: 0, padding: '12px 16px', background: 'var(--t-surface)', border: '1px solid var(--t-line)', position: 'relative' }}>
        <div style={{ position: 'absolute', top: -1, left: -1, width: 8, height: 8, borderTop: '1px solid var(--t-cyan)', borderLeft: '1px solid var(--t-cyan)' }} />
        <div style={{ position: 'absolute', bottom: -1, right: -1, width: 8, height: 8, borderBottom: '1px solid var(--t-cyan)', borderRight: '1px solid var(--t-cyan)' }} />

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-cyan)', letterSpacing: '0.15em' }}>RESEARCH_CHAIN</span>
          <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-3)' }}>5 STEPS · 4/5 · 4.2s</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-3)' }}>[ESC] CANCEL</span>
        </div>

        {tools.map((tl, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '4px 0', fontSize: 11.5, fontFamily: '"JetBrains Mono"' }}>
            <span style={{ color: 'var(--t-ink-3)', width: 22 }}>{tl.i}</span>
            <span style={{
              width: 14, color: tl.status === 'run' ? 'var(--t-amber)' : 'var(--t-down)',
              textShadow: tl.status === 'run' ? '0 0 4px var(--t-amber)' : 'none'
            }}>{tl.status === 'run' ? '◐' : '◉'}</span>
            <span style={{ color: 'var(--t-cyan-d)', minWidth: 140 }}>{tl.name}</span>
            <span style={{ color: 'var(--t-ink-3)', fontFamily: '"Inter"', width: 70 }}>{tl.cn}</span>
            <span style={{ color: 'var(--t-ink-3)', width: 40, textAlign: 'right' }}>{tl.t}</span>
            <span style={{ color: 'var(--t-ink-3)' }}>→</span>
            <span style={{ color: tl.status === 'run' ? 'var(--t-ink-3)' : 'var(--t-ink)', flex: 1 }}>{tl.result}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

// ───── brief card ─────
const TechBriefCard = () => (
  <div style={{ display: 'flex', gap: 12 }}>
    <TechAvatar />
    <div style={{
      flex: 1, minWidth: 0, position: 'relative',
      background: 'linear-gradient(180deg, rgba(77,213,255,0.04), rgba(255,94,154,0.03))',
      border: '1px solid var(--t-cyan)',
      boxShadow: '0 0 0 1px rgba(77,213,255,0.06), 0 0 24px rgba(77,213,255,0.08) inset',
    }}>
      {/* corner brackets */}
      {['top: -1, left: -1, borderTop, borderLeft', 'top: -1, right: -1, borderTop, borderRight', 'bottom: -1, left: -1, borderBottom, borderLeft', 'bottom: -1, right: -1, borderBottom, borderRight'].map((_, i) => {
        const pos = i < 2 ? 'top' : 'bottom';
        const side = i % 2 === 0 ? 'left' : 'right';
        return (
          <div key={i} style={{
            position: 'absolute', [pos]: -1, [side]: -1, width: 12, height: 12,
            [`border${pos.charAt(0).toUpperCase() + pos.slice(1)}`]: '2px solid var(--t-cyan)',
            [`border${side.charAt(0).toUpperCase() + side.slice(1)}`]: '2px solid var(--t-cyan)',
          }} />
        );
      })}

      {/* header */}
      <div style={{ padding: '14px 18px 12px', borderBottom: '1px solid var(--t-line-2)', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-cyan)', letterSpacing: '0.15em' }}>[STOCK_BRIEF]</span>
            <span style={{ fontSize: 22, fontWeight: 600, color: 'var(--t-ink)' }}>宁德时代</span>
            <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 11, color: 'var(--t-ink-3)' }}>300750.SZ</span>
          </div>
          <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-3)', marginTop: 4 }}>
            电力设备/电池 · MC=14,206亿 · FC=8,142亿
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 28, color: 'var(--t-up)', fontWeight: 600, textShadow: '0 0 12px rgba(255,91,110,0.4)' }}>325.10</div>
          <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 12, color: 'var(--t-up)' }}>+7.04 · +2.21%</div>
        </div>
      </div>

      {/* grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
        <TechBriefRegion title="QUOTE.METRICS" cite="01">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
            {[
              { l: 'VOL_RAT', v: '1.42' }, { l: 'TURN',   v: '2.18%' }, { l: 'AMP',  v: '3.84%' },
              { l: 'PE_TTM', v: '21.4' }, { l: 'PB',     v: '4.6' },   { l: 'ROE',  v: '24.6%' },
            ].map((m, i) => (
              <div key={i}>
                <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)', letterSpacing: '0.1em' }}>{m.l}</div>
                <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 14, color: 'var(--t-ink)', marginTop: 2 }}>{m.v}</div>
              </div>
            ))}
          </div>
        </TechBriefRegion>

        <TechBriefRegion title="FUND_FLOW.TODAY" cite="02" borderL>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 8 }}>
            <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 20, color: 'var(--t-up)', fontWeight: 600 }}>+4.82</span>
            <span style={{ fontFamily: '"Inter"', fontSize: 10, color: 'var(--t-ink-2)' }}>亿 NET_IN</span>
            <span style={{ marginLeft: 'auto', fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-3)' }}>D-1: +1.9</span>
          </div>
          {[
            { l: 'XL', v: '+2.1', pct: 70, up: true },
            { l: 'L',  v: '+1.0', pct: 33, up: true },
            { l: 'M',  v: '+1.7', pct: 56, up: true },
            { l: 'S',  v: '-0.4', pct: 13, up: false },
          ].map((b, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, fontSize: 10, fontFamily: '"JetBrains Mono"' }}>
              <span style={{ color: 'var(--t-ink-3)', width: 18 }}>{b.l}</span>
              <div style={{ flex: 1, height: 3, background: 'rgba(255,255,255,0.05)', position: 'relative' }}>
                <div style={{ width: `${b.pct}%`, height: '100%', background: b.up ? 'var(--t-up)' : 'var(--t-down)', boxShadow: `0 0 6px ${b.up ? 'rgba(255,91,110,0.5)' : 'rgba(43,212,168,0.5)'}` }} />
              </div>
              <span style={{ color: b.up ? 'var(--t-up)' : 'var(--t-down)', width: 36, textAlign: 'right' }}>{b.v}亿</span>
            </div>
          ))}
        </TechBriefRegion>

        <TechBriefRegion title="NEWS.RECENT" sub="14 / 7D" cite="03" borderT>
          {[
            { t: '签订 19 GWh 海外储能订单', s: 'cninfo · 11-04', tone: 'up' },
            { t: '中信 BUY · TP=360', s: 'citics · 11-12', tone: 'up' },
            { t: '10 月电池装车 +51% YoY', s: 'caam · 11-12', tone: 'up' },
          ].map((n, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4px 0', borderTop: i ? '1px dashed var(--t-line)' : 'none' }}>
              <span style={{ color: n.tone === 'up' ? 'var(--t-up)' : 'var(--t-down)' }}>◆</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, color: 'var(--t-ink)' }}>{n.t}</div>
                <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)', marginTop: 1 }}>{n.s}</div>
              </div>
            </div>
          ))}
        </TechBriefRegion>

        <TechBriefRegion title="CHAIN.PEERS" cite="04" borderL borderT>
          <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)', marginBottom: 4, letterSpacing: '0.1em' }}>PEER.GROSS_MARGIN</div>
          {[
            { n: '宁德时代', v: '28.4', pct: 95, focus: true },
            { n: '亿纬锂能', v: '17.2', pct: 58 },
            { n: '中创新航', v: '11.8', pct: 39 },
          ].map((r, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, fontSize: 11 }}>
              <span style={{ color: r.focus ? 'var(--t-cyan-d)' : 'var(--t-ink-2)', width: 60 }}>{r.n}</span>
              <div style={{ flex: 1, height: 3, background: 'rgba(255,255,255,0.05)' }}>
                <div style={{ width: `${r.pct}%`, height: '100%', background: r.focus ? 'var(--t-cyan)' : 'var(--t-ink-3)', boxShadow: r.focus ? '0 0 6px var(--t-cyan)' : 'none' }} />
              </div>
              <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink)', width: 32, textAlign: 'right' }}>{r.v}%</span>
            </div>
          ))}
          <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px dashed var(--t-line)', display: 'flex', alignItems: 'center', gap: 6, fontFamily: '"JetBrains Mono"', fontSize: 10 }}>
            <span style={{ color: 'var(--t-ink-3)' }}>XQ.SENT</span>
            <div style={{ flex: 1, height: 3, background: 'rgba(255,255,255,0.05)' }}>
              <div style={{ width: '68%', height: '100%', background: 'var(--t-up)' }} />
            </div>
            <span style={{ color: 'var(--t-up)' }}>BULL 68%</span>
          </div>
        </TechBriefRegion>
      </div>

      {/* action bar */}
      <div style={{ padding: '8px 18px', display: 'flex', gap: 14, fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-2)', borderTop: '1px solid var(--t-line-2)' }}>
        <span style={{ cursor: 'pointer' }}>[S] SAVE.MD</span>
        <span style={{ cursor: 'pointer' }}>[+] WATCH</span>
        <span style={{ cursor: 'pointer' }}>[A] ALERT</span>
        <span style={{ cursor: 'pointer', color: 'var(--t-amber)' }}>[R] RUN_REPORT ▸ 6m · CONFIRM</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: 'var(--t-ink-3)' }}>REFS 01·02·03·04</span>
      </div>
    </div>
  </div>
);

const TechBriefRegion = ({ title, sub, cite, children, borderL, borderT }) => (
  <div style={{ padding: '12px 16px', borderLeft: borderL ? '1px solid var(--t-line-2)' : 'none', borderTop: borderT ? '1px solid var(--t-line-2)' : 'none' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
      <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-cyan)', letterSpacing: '0.15em' }}>{title}</span>
      {sub && <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)' }}>{sub}</span>}
      <span style={{ flex: 1 }} />
      {cite && <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-cyan)', padding: '0 4px', border: '1px solid var(--t-cyan)' }}>{cite}</span>}
    </div>
    {children}
  </div>
);

// ───── summary ─────
const TechSummary = () => (
  <div style={{ display: 'flex', gap: 12 }}>
    <TechAvatar />
    <div style={{ flex: 1, fontSize: 14, lineHeight: 1.85, color: 'var(--t-ink)', padding: '6px 0' }}>
      Q3 利润 <span style={{ color: 'var(--t-up)', fontFamily: '"JetBrains Mono"', fontWeight: 600 }}>+25.9% YoY</span> 超预期，毛利率回升 28.4%<TCite n="01"/>；
      今日主力净 <span style={{ color: 'var(--t-up)', fontFamily: '"JetBrains Mono"', fontWeight: 600 }}>+4.82亿</span>，连续 3 日加仓<TCite n="02"/>。
      催化：<span style={{ color: 'var(--t-cyan-d)' }}>19 GWh 海外储能订单</span> + 中信 TP=360<TCite n="03"/>；
      同行毛利率仍处第一梯队<TCite n="04"/>。
      短线 SENTIMENT=BULL，建议
      <span style={{ background: 'linear-gradient(90deg, rgba(77,213,255,0.18), rgba(77,213,255,0.04))', padding: '0 6px', borderBottom: '1px solid var(--t-cyan)' }}>分批跟进 · 关注 330 压力</span>。
      <span style={{ marginLeft: 8, padding: '2px 6px', background: 'var(--t-surface-2)', border: '1px solid var(--t-line)', color: 'var(--t-amber)', fontSize: 10, fontFamily: '"JetBrains Mono"' }}>STREAMING…</span>
    </div>
  </div>
);

const TCite = ({ n }) => (
  <sup style={{
    display: 'inline-block', padding: '0 4px', marginLeft: 2,
    background: 'var(--t-cyan)', color: 'var(--t-bg)',
    fontFamily: '"JetBrains Mono"', fontSize: 8, fontWeight: 700, cursor: 'pointer', verticalAlign: 2,
    boxShadow: '0 0 6px rgba(77,213,255,0.5)'
  }}>{n}</sup>
);

// ───── composer ─────
const TechComposer = () => (
  <div style={{ padding: '12px 36px 16px', borderTop: '1px solid var(--t-line)', background: 'rgba(8,12,22,0.5)', backdropFilter: 'blur(20px)', flexShrink: 0, position: 'relative', zIndex: 1 }}>
    <div style={{ position: 'relative', border: '1px solid var(--t-line-2)', background: 'var(--t-surface)', padding: '12px 16px' }}>
      <div style={{ position: 'absolute', top: -1, left: -1, width: 10, height: 10, borderTop: '1px solid var(--t-cyan)', borderLeft: '1px solid var(--t-cyan)' }} />
      <div style={{ position: 'absolute', bottom: -1, right: -1, width: 10, height: 10, borderBottom: '1px solid var(--t-cyan)', borderRight: '1px solid var(--t-cyan)' }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
        <span style={{ color: 'var(--t-cyan)', fontFamily: '"JetBrains Mono"' }}>▸</span>
        <span style={{ color: 'var(--t-ink-3)', flex: 1 }}>继续追问，或 /command…</span>
        <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-3)' }}>[TAB] complete · [↵] send</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
        <div style={{ display: 'flex', gap: 6, flex: 1 }}>
          {['⊟ UPLOAD', '@ STOCK', '⌗ BOARD', '/ CMD'].map((x, i) => (
            <span key={i} style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-2)', padding: '3px 7px', border: '1px solid var(--t-line-2)', cursor: 'pointer', letterSpacing: '0.06em' }}>{x}</span>
          ))}
        </div>
        <button style={{
          background: 'linear-gradient(135deg, var(--t-cyan), var(--t-magenta))', color: 'var(--t-bg)',
          border: 'none', padding: '6px 18px', fontFamily: '"Space Grotesk"', fontWeight: 600, fontSize: 11, letterSpacing: '0.15em', cursor: 'pointer',
          clipPath: 'polygon(8% 0, 100% 0, 92% 100%, 0 100%)'
        }}>SEND →</button>
      </div>
    </div>
  </div>
);

// ───── 右栏 ─────
const TechRightRail = () => (
  <aside style={{
    width: 312, borderLeft: '1px solid var(--t-line)', display: 'flex', flexDirection: 'column', flexShrink: 0,
    background: 'rgba(8,12,22,0.5)', backdropFilter: 'blur(20px)', position: 'relative', zIndex: 1
  }}>
    {/* header */}
    <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--t-line)', display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ width: 6, height: 6, background: 'var(--t-amber)', boxShadow: '0 0 8px var(--t-amber)', borderRadius: '50%' }} />
      <span style={{ fontSize: 13, color: 'var(--t-ink)', fontWeight: 500 }}>WATCH.DAEMON</span>
      <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-3)' }}>5m</span>
      <span style={{ flex: 1 }} />
      <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-cyan)', cursor: 'pointer' }}>/watch ▾</span>
    </div>

    {/* fired alert */}
    <div style={{ padding: '14px 18px' }}>
      <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)', letterSpacing: '0.2em', marginBottom: 8 }}>FIRED · 13:54:08</div>
      <div style={{
        position: 'relative',
        border: '1px solid var(--t-amber)', background: 'linear-gradient(180deg, rgba(255,200,87,0.10), rgba(255,200,87,0.02))',
        padding: '12px 14px', boxShadow: '0 0 20px rgba(255,200,87,0.12)'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: 'var(--t-amber)', fontSize: 12 }}>▲</span>
          <span style={{ color: 'var(--t-ink)', fontWeight: 500 }}>中际旭创</span>
          <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-3)' }}>300308</span>
          <span style={{ marginLeft: 'auto', fontFamily: '"JetBrains Mono"', color: 'var(--t-up)', fontWeight: 600, fontSize: 13 }}>+4.12%</span>
        </div>
        <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-2)', marginTop: 8 }}>
          RULE → <span style={{ color: 'var(--t-amber)' }}>PCT_ABV 4</span>
        </div>
        <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 10, color: 'var(--t-ink-3)', marginTop: 3 }}>
          PRC=142.55 · VOL×2.1 · T=13:54
        </div>
        <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          {['[A] ASK →', '[P] PAUSE', '[D] DROP'].map((x, i) => (
            <span key={i} style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-2)', padding: '3px 7px', border: '1px solid var(--t-line-2)', cursor: 'pointer' }}>{x}</span>
          ))}
        </div>
      </div>
    </div>

    {/* active rules */}
    <div style={{ padding: '8px 18px 12px' }}>
      <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)', letterSpacing: '0.2em', marginBottom: 10 }}>ACTIVE · 3</div>
      {[
        { n: '贵州茅台', rule: 'PRC < 1200', cur: '1684',   pct: 28, far: '+40.3%' },
        { n: '宁德时代', rule: 'PCT ≥ 5%',   cur: '+2.21%', pct: 44, far: 'REM 2.79' },
        { n: '比亚迪',   rule: 'PRC > 300',  cur: '281.40', pct: 92, far: 'REM 6.62%' },
      ].map((a, i) => (
        <div key={i} style={{ padding: '10px 0', borderTop: i ? '1px solid var(--t-line)' : 'none' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ fontSize: 12, color: 'var(--t-ink)' }}>{a.n}</span>
            <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)' }}>{a.rule}</span>
            <span style={{ marginLeft: 'auto', fontFamily: '"JetBrains Mono"', fontSize: 11, color: 'var(--t-ink-2)' }}>{a.cur}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 5 }}>
            <div style={{ flex: 1, height: 2, background: 'rgba(255,255,255,0.06)' }}>
              <div style={{ width: `${a.pct}%`, height: '100%', background: 'var(--t-cyan)', boxShadow: '0 0 4px var(--t-cyan)' }} />
            </div>
            <span style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)' }}>{a.far}</span>
          </div>
        </div>
      ))}
    </div>

    {/* citations */}
    <div style={{ padding: '14px 18px', borderTop: '1px solid var(--t-line)' }}>
      <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)', letterSpacing: '0.2em', marginBottom: 10 }}>REFS · TASK_0428 · 4</div>
      {[
        { n: '01', src: 'realtime_quote', t: 'ths · 14:17',     d: '325.10 +2.21%' },
        { n: '02', src: 'ths_fund_flow',  t: 'ths · 14:17',     d: 'net_in 4.82亿' },
        { n: '03', src: 'news_query',     t: 'sqlite · 7d',     d: '14 docs · cninfo/citics/caam' },
        { n: '04', src: 'chain_for',      t: 'qlib.industry',   d: 'peers=5 up=6 down=8' },
      ].map((c, i) => (
        <div key={i} style={{ display: 'flex', gap: 10, padding: '7px 0', borderTop: i ? '1px solid var(--t-line)' : 'none' }}>
          <span style={{
            fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-cyan)',
            padding: '1px 5px', background: 'rgba(77,213,255,0.1)', border: '1px solid var(--t-cyan)', flexShrink: 0,
            height: 18, boxShadow: '0 0 6px rgba(77,213,255,0.3)'
          }}>{c.n}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <code style={{ fontFamily: '"JetBrains Mono"', fontSize: 11, color: 'var(--t-cyan-d)' }}>{c.src}</code>
            <div style={{ fontSize: 11, color: 'var(--t-ink-2)', marginTop: 1 }}>{c.d}</div>
            <div style={{ fontFamily: '"JetBrains Mono"', fontSize: 9, color: 'var(--t-ink-3)', marginTop: 1 }}>{c.t}</div>
          </div>
        </div>
      ))}
    </div>

    <div style={{ flex: 1 }} />

    {/* footer */}
    <div style={{ padding: '10px 18px', borderTop: '1px solid var(--t-line)', display: 'flex', alignItems: 'center', gap: 8, fontFamily: '"JetBrains Mono"', fontSize: 10 }}>
      <span style={{ width: 6, height: 6, background: 'var(--t-down)', boxShadow: '0 0 6px var(--t-down)', borderRadius: '50%' }} />
      <span style={{ color: 'var(--t-ink-2)' }}>ALL SOURCES ≤ 4m</span>
      <span style={{ flex: 1 }} />
      <span style={{ color: 'var(--t-cyan)', cursor: 'pointer' }}>[R] REFRESH</span>
    </div>
  </aside>
);

window.TechStream = TechStream;
