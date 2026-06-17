// 乙 · 报 — 任务报告变体（Perplexity-like，左任务/中长报/右引用源）
// 1440 x 900
// 亮点：右侧引用以"印章式"脚注呈现

const VariantReport = () => {
  const W = 1440, H = 900;

  const Cite = ({ n }) => (
    <sup style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: 17, height: 17, background: 'var(--yin)', color: 'var(--paper)',
      fontFamily: 'var(--serif)', fontSize: 9, fontWeight: 500,
      margin: '0 2px', verticalAlign: 1, cursor: 'pointer'
    }}>{n}</sup>
  );

  return (
    <div style={{ width: W, height: H, display: 'flex', flexDirection: 'column', fontFamily: 'var(--sans)', color: 'var(--ink)', background: 'var(--paper)' }} className="paper-bg">

      {/* ───── 顶栏 ───── */}
      <header style={{ height: 60, padding: '0 32px', borderBottom: '1px solid var(--ink)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 28 }}>
          <Brandmark subtitle="GUAN LAN" small />
          <div style={{ width: 1, height: 24, background: 'var(--line)' }} />
          <nav style={{ display: 'flex', gap: 22 }}>
            {[
              { t: '任务', n: '12', active: true }, { t: '盯盘', n: '6' }, { t: '研报库' }, { t: '复盘' },
            ].map((x, i) => (
              <div key={i} style={{ position: 'relative', paddingBottom: 4, borderBottom: x.active ? '2px solid var(--ink)' : '2px solid transparent', fontFamily: 'var(--serif)', fontSize: 14, color: x.active ? 'var(--ink)' : 'var(--ink-2)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}>
                {x.t}
                {x.n && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{x.n}</span>}
              </div>
            ))}
          </nav>
        </div>
        <MarketTicker items={[
          { name: '上证', value: '3,287.42', delta: '+0.46%' },
          { name: '深成', value: '10,524.11', delta: '+0.82%' },
          { name: '创业', value: '2,114.08', delta: '+1.12%' },
          { name: '收盘前', value: '00:42:17' },
        ]} />
      </header>

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>

        {/* ───── 左栏：任务索引 ───── */}
        <aside style={{ width: 232, borderRight: '1px solid var(--line)', padding: '20px 0', overflowY: 'hidden', flexShrink: 0 }}>
          <div style={{ padding: '0 22px 10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em' }}>本次任务</span>
            <span style={{ fontSize: 14, color: 'var(--ink-3)' }}>＋</span>
          </div>

          {/* 主任务 */}
          <div style={{ padding: '8px 22px 14px', borderLeft: '2px solid var(--yin)', background: 'rgba(168,57,45,0.04)' }}>
            <div className="serif" style={{ fontSize: 14, fontWeight: 500, lineHeight: 1.45 }}>
              宁德时代基本面 +<br/>锂电行业景气
            </div>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 6, letterSpacing: '0.08em' }}>
              04:38 已完成 · 6 节
            </div>
          </div>

          {/* TOC */}
          <div style={{ padding: '14px 22px 0', borderLeft: '2px solid transparent' }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em', marginBottom: 10 }}>报告目录</div>
            {[
              { n: '01', t: '执行摘要', active: true },
              { n: '02', t: '公司概况' },
              { n: '03', t: '财务表现' },
              { n: '04', t: '行业景气度' },
              { n: '05', t: '同业比较' },
              { n: '06', t: '风险与建议' },
            ].map((s, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, padding: '7px 0', cursor: 'pointer', alignItems: 'baseline' }}>
                <span className="mono" style={{ fontSize: 9, color: s.active ? 'var(--yin)' : 'var(--ink-3)', letterSpacing: '0.05em' }}>{s.n}</span>
                <span className="serif" style={{ fontSize: 13, color: s.active ? 'var(--ink)' : 'var(--ink-2)', fontWeight: s.active ? 500 : 400 }}>{s.t}</span>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 24, padding: '14px 22px', borderTop: '1px solid var(--line-soft)' }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em', marginBottom: 12 }}>最近任务</div>
            {[
              { t: '北向资金一周流向', s: '2 小时前' },
              { t: '半导体国产替代', s: '昨日' },
              { t: '今日异动股扫描', s: '昨日' },
              { t: '比亚迪财报对比', s: '2 日前' },
            ].map((it, i) => (
              <div key={i} style={{ padding: '7px 0', cursor: 'pointer' }}>
                <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)' }}>{it.t}</div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>{it.s}</div>
              </div>
            ))}
          </div>
        </aside>

        {/* ───── 中央：长报告 ───── */}
        <main style={{ flex: 1, overflow: 'hidden', minWidth: 0, padding: '36px 56px 24px' }}>

          {/* 报告题头 */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', paddingBottom: 16, borderBottom: '2px solid var(--ink)' }}>
            <div>
              <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.25em', marginBottom: 10 }}>
                深度研究 · NO.0428 · 2026-05-21
              </div>
              <h1 className="serif" style={{ fontSize: 32, fontWeight: 500, letterSpacing: '-0.01em', margin: 0, lineHeight: 1.15 }}>
                宁德时代 · 三季报后基本面与行业景气度
              </h1>
              <div style={{ display: 'flex', gap: 18, marginTop: 14, fontSize: 11, color: 'var(--ink-2)' }} className="mono">
                <span>CATL · 300750.SZ</span>
                <span>14 处引用</span>
                <span>5 张图表</span>
                <span>研究耗时 04:38</span>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              {[{t:'PDF',i:'↧'},{t:'分享',i:'↗'},{t:'追问',i:'＋'}].map((x,i) => (
                <button key={i} style={{ padding: '8px 14px', border: '1px solid var(--ink-2)', background: 'transparent', fontFamily: 'var(--serif)', fontSize: 12, letterSpacing: '0.08em', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, color: 'var(--ink-1)' }}>
                  <span className="mono" style={{ fontSize: 10 }}>{x.i}</span> {x.t}
                </button>
              ))}
            </div>
          </div>

          {/* 摘要 */}
          <section style={{ marginTop: 28 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 16 }}>
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>01</span>
              <h2 className="serif" style={{ fontSize: 19, fontWeight: 500, margin: 0 }}>执行摘要</h2>
              <hr className="ink-rule" style={{ flex: 1 }} />
            </div>
            <p className="serif" style={{ fontSize: 15, lineHeight: 1.95, color: 'var(--ink)', margin: 0, textWrap: 'pretty' }}>
              公司 2024 Q3 营收 922.8 亿元（YoY <span className="up mono">+12.4%</span>），归母净利润 131.4 亿元（YoY <span className="up mono">+25.9%</span>），均超彭博一致预期约 6%<Cite n="1"/><Cite n="3"/>。
              利润增速显著高于营收，核心驱动来自<strong style={{ fontWeight: 600 }}>毛利率回升至 28.4%</strong>（环比 +1.8 pct），主要受益于碳酸锂价格中枢下行、产能利用率回升、储能业务占比首次突破 25%<Cite n="4"/>。
              行业层面，2024 年 10 月国内动力电池装车量同比 <span className="up mono">+51%</span>，储能装机同比 <span className="up mono">+86%</span><Cite n="7"/><Cite n="8"/>，景气度处于
              <span style={{ background: 'linear-gradient(180deg, transparent 60%, rgba(138,111,63,0.22) 60%)' }}>"复苏中段"</span>，但二线厂商扩产仍激进，需警惕 2025H2 价格战风险<Cite n="11"/>。
            </p>
            <div style={{ marginTop: 18, padding: '14px 18px', borderLeft: '2px solid var(--yin)', background: 'rgba(168,57,45,0.04)', display: 'flex', alignItems: 'center', gap: 18 }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--yin)', letterSpacing: '0.15em', flexShrink: 0 }}>建议</span>
              <span className="serif" style={{ fontSize: 14, color: 'var(--ink)' }}>分批建仓、关注 Q4 出货指引及海外储能新订单。目标区间 <span className="mono">295 - 340</span>。</span>
            </div>
          </section>

          {/* 财务表现节选 */}
          <section style={{ marginTop: 36 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 16 }}>
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>03</span>
              <h2 className="serif" style={{ fontSize: 19, fontWeight: 500, margin: 0 }}>财务表现</h2>
              <hr className="ink-rule" style={{ flex: 1 }} />
            </div>

            <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--mono)', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--ink)' }}>
                  <th style={{ textAlign: 'left', padding: '8px 0', fontFamily: 'var(--serif)', fontWeight: 500, fontSize: 13, color: 'var(--ink-2)' }}>指标</th>
                  {['2023Q3', '2024Q1', '2024Q2', '2024Q3', 'YoY', '一致预期'].map(h => (
                    <th key={h} style={{ textAlign: 'right', padding: '8px 14px 8px 0', fontFamily: 'var(--serif)', fontWeight: 500, fontSize: 13, color: 'var(--ink-2)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  ['营收（亿）', '820.5', '797.7', '870.0', '922.8', '+12.4%', '870.2'],
                  ['归母净利（亿）', '104.4', '105.1', '123.5', '131.4', '+25.9%', '124.0'],
                  ['毛利率', '22.4%', '26.4%', '26.6%', '28.4%', '+6.0 pct', '26.8%'],
                  ['净利率', '12.7%', '13.2%', '14.2%', '14.2%', '+1.5 pct', '14.2%'],
                  ['经营现金流（亿）', '198.6', '284.3', '203.8', '274.6', '+38.2%', '—'],
                  ['ROE（TTM）', '20.1%', '21.5%', '22.4%', '24.6%', '+4.5 pct', '—'],
                ].map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--line-soft)' }}>
                    <td style={{ padding: '11px 0', fontFamily: 'var(--serif)', color: 'var(--ink)' }}>{r[0]}</td>
                    {r.slice(1).map((c, j) => {
                      const isUp = j === 4 && !c.startsWith('-') && c !== '—';
                      const isBeat = j === 5 && i < 3;
                      return (
                        <td key={j} style={{
                          textAlign: 'right', padding: '11px 14px 11px 0',
                          color: isUp ? 'var(--zhu)' : isBeat ? 'var(--ink-2)' : 'var(--ink-1)',
                          fontWeight: j === 3 ? 600 : 400
                        }}>{c}</td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </main>

        {/* ───── 右栏：印章式引用源 ───── */}
        <aside style={{ width: 304, borderLeft: '1px solid var(--line)', padding: '24px 22px', overflow: 'hidden', flexShrink: 0, background: 'rgba(241,234,217,0.4)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em' }}>引用源 · 14</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)', cursor: 'pointer' }}>全部 ▾</span>
          </div>

          {[
            { n: 1, type: '财报', src: '宁德时代 · 2024 三季报', t: '营业收入 922.8 亿元，归母净利润 131.4 亿元', d: '2024-10-18', tag: '巨潮' },
            { n: 3, type: '研报', src: '中信证券 · 锂电深度', t: '"宁德 Q3 利润超预期，毛利率拐点确立"', d: '10-21', tag: '中信' },
            { n: 4, type: '公告', src: '产品订单 · 储能项目', t: '签订 19 GWh 海外储能订单', d: '11-04', tag: '深交所' },
            { n: 7, type: '数据', src: '中汽协 · 动力电池月报', t: '10 月装车量 59.2 GWh，同比 +51%', d: '11-12', tag: '中汽协' },
            { n: 8, type: '数据', src: 'CNESA · 储能装机', t: '前三季度新型储能装机 21.7 GW', d: '11-08', tag: 'CNESA' },
            { n: 11, type: '研报', src: '某外资 · 锂电产能监测', t: '"二线厂商 2025 年产能扩张 +60%"', d: '11-15', tag: '海外' },
          ].map((c, i) => (
            <div key={i} style={{ display: 'flex', gap: 12, padding: '12px 0', borderBottom: i < 5 ? '1px solid var(--line-soft)' : 'none' }}>
              {/* 印章序号 */}
              <div style={{ width: 28, height: 28, background: 'var(--yin)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 12, fontWeight: 500, flexShrink: 0 }}>{c.n}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 3 }}>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>{c.type}</span>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>· {c.d}</span>
                </div>
                <div className="serif" style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink)', marginBottom: 4 }}>{c.src}</div>
                <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.55, textWrap: 'pretty' }}>{c.t}</div>
              </div>
            </div>
          ))}

          <div style={{ marginTop: 14, padding: '10px 12px', background: 'var(--paper-2)', display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>＋ 8</span>
            <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)' }}>条新闻、互动易、券商研报</span>
          </div>
        </aside>
      </div>
    </div>
  );
};

window.VariantReport = VariantReport;
