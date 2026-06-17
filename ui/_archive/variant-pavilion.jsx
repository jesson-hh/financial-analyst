// 丙 · 阁 — 多面板工作台（信息密集）
// 1440 x 900
// 亮点：K 线轴用毛笔笔触刻度；左侧细图标导航

const VariantPavilion = () => {
  const W = 1440, H = 900;

  const candleData = [
    {o: 240, c: 252, h: 256, l: 238}, {o: 252, c: 248, h: 258, l: 245},
    {o: 248, c: 261, h: 264, l: 247}, {o: 261, c: 258, h: 263, l: 254},
    {o: 258, c: 270, h: 274, l: 257}, {o: 270, c: 275, h: 278, l: 266},
    {o: 275, c: 268, h: 277, l: 264}, {o: 268, c: 280, h: 283, l: 267},
    {o: 280, c: 285, h: 288, l: 277}, {o: 285, c: 281, h: 289, l: 278},
    {o: 281, c: 292, h: 295, l: 279}, {o: 292, c: 298, h: 301, l: 290},
    {o: 298, c: 295, h: 302, l: 292}, {o: 295, c: 305, h: 308, l: 293},
    {o: 305, c: 312, h: 316, l: 303}, {o: 312, c: 308, h: 318, l: 306},
    {o: 308, c: 315, h: 320, l: 305}, {o: 315, c: 322, h: 326, l: 312},
    {o: 322, c: 318, h: 326, l: 314}, {o: 318, c: 325, h: 329, l: 316},
  ];

  return (
    <div style={{ width: W, height: H, display: 'flex', fontFamily: 'var(--sans)', color: 'var(--ink)', background: 'var(--paper)' }} className="paper-bg">

      {/* ───── 极窄左侧导航 ───── */}
      <aside style={{ width: 56, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '14px 0', flexShrink: 0, background: 'rgba(241,234,217,0.6)' }}>
        <div className="seal" style={{ width: 28, height: 28, fontSize: 15 }}>觀</div>
        <div style={{ width: 24, height: 1, background: 'var(--line)', margin: '14px 0' }} />
        {[
          { i: '⌧', t: '工作台', active: true },
          { i: '◫', t: '盯盘' },
          { i: '⌬', t: '自选' },
          { i: '※', t: '研报' },
          { i: '⌗', t: '板块' },
          { i: '⊟', t: '回测' },
          { i: '⎈', t: '设置' },
        ].map((x, i) => (
          <div key={i} title={x.t} style={{
            width: 36, height: 36, marginBottom: 4, display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'var(--serif)', fontSize: 16, color: x.active ? 'var(--ink)' : 'var(--ink-3)',
            background: x.active ? 'var(--paper-2)' : 'transparent', cursor: 'pointer', borderLeft: x.active ? '2px solid var(--yin)' : '2px solid transparent'
          }}>{x.i}</div>
        ))}
        <div style={{ flex: 1 }} />
        <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--ink-1)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 12 }}>陈</div>
      </aside>

      {/* ───── 主区 ───── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>

        {/* 顶栏：搜索 + 大盘条 */}
        <header style={{ height: 56, padding: '0 22px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 22, flexShrink: 0, background: 'rgba(241,234,217,0.5)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: 280, padding: '7px 12px', border: '1px solid var(--ink-2)', background: 'var(--paper)' }}>
            <span className="mono" style={{ color: 'var(--ink-3)' }}>⌕</span>
            <span className="serif" style={{ fontSize: 13, color: 'var(--ink-3)', flex: 1 }}>问个问题，或输入代码、名称…</span>
            <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', padding: '1px 5px', border: '1px solid var(--line)' }}>⌘ K</span>
          </div>
          <div style={{ display: 'flex', gap: 22, fontFamily: 'var(--mono)', fontSize: 11.5 }}>
            {[
              { n: '上证', v: '3,287.42', d: '+0.46', vol: '4,892亿' },
              { n: '深成', v: '10,524.11', d: '+0.82', vol: '5,217亿' },
              { n: '创业', v: '2,114.08', d: '+1.12', vol: '2,684亿' },
              { n: '沪深300', v: '3,892.51', d: '+0.31' },
              { n: '北向', v: '+38.4亿', d: '' },
            ].map((x, i) => (
              <div key={i}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                  <span style={{ color: 'var(--ink-2)', fontSize: 10 }}>{x.n}</span>
                  <span style={{ color: 'var(--ink)', fontWeight: 500 }}>{x.v}</span>
                  {x.d && <span className={x.d.startsWith('-') ? 'down' : 'up'}>{x.d}%</span>}
                </div>
                {x.vol && <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 1 }}>{x.vol}</div>}
              </div>
            ))}
          </div>
          <div style={{ flex: 1 }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: 'var(--ink-2)' }} className="mono">
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--zhu)' }} />
            <span>开盘中 · 14:17</span>
            <span style={{ width: 1, height: 12, background: 'var(--line)' }} />
            <span>本地 Qwen-72B</span>
          </div>
        </header>

        {/* ───── 工作台栅格 ───── */}
        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.05fr 1fr', gridTemplateRows: '1.2fr 1fr', gap: 12, padding: 12, minHeight: 0 }}>

          {/* ◉ 左上：对话面板 */}
          <Panel title="对话 · 宁德时代研究" sub="04:38 · 已引用 14 处" tag="主">
            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: 12, fontSize: 13 }}>
              {/* 用户气泡 */}
              <div style={{ alignSelf: 'flex-end', maxWidth: '85%', padding: '8px 12px', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12.5, lineHeight: 1.6 }}>
                分析一下宁德时代最近的基本面，结合行业景气度。
              </div>
              {/* AI 内联步骤 */}
              <div style={{ fontFamily: 'var(--serif)', fontSize: 12.5, color: 'var(--ink-1)', lineHeight: 1.75, padding: '0 4px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--ink-3)', fontSize: 11, marginBottom: 6 }} className="mono">
                  <span style={{ width: 6, height: 6, background: 'var(--ink)' }} />
                  <span>已读 14 份资料 · 04:38</span>
                </div>
                公司 Q3 利润超预期，
                <span style={{ background: 'linear-gradient(180deg, transparent 60%, rgba(185,74,61,0.18) 60%)' }}>毛利率回升至 28.4%</span>
                ，储能业务占比首破 25%。建议
                <span style={{ background: 'linear-gradient(180deg, transparent 60%, rgba(74,107,92,0.18) 60%)' }}>分批建仓</span>
                ，关注 Q4 出货指引。
              </div>
              {/* mini 数据条 */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1, background: 'var(--line)', border: '1px solid var(--line)' }}>
                {[
                  { l: '营收', v: '922.8亿', d: '+12.4%' },
                  { l: '净利', v: '131.4亿', d: '+25.9%' },
                  { l: '毛利率', v: '28.4%', d: '+3.1pct' },
                ].map((x, i) => (
                  <div key={i} style={{ background: 'var(--paper)', padding: '8px 10px' }}>
                    <div style={{ fontSize: 10, color: 'var(--ink-2)' }}>{x.l}</div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginTop: 2 }}>
                      <span className="mono" style={{ fontSize: 14, fontWeight: 500 }}>{x.v}</span>
                      <span className="up mono" style={{ fontSize: 9 }}>{x.d}</span>
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 6 }} className="mono">
                {['查看完整报告 →', '加入持仓', '继续追问'].map((x, i) => (
                  <span key={i} style={{ fontSize: 10, color: 'var(--ink-2)', padding: '3px 8px', border: '1px solid var(--line)', cursor: 'pointer' }}>{x}</span>
                ))}
              </div>
            </div>
            {/* 输入 */}
            <div style={{ marginTop: 10, padding: '8px 10px', border: '1px solid var(--ink-2)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span className="serif" style={{ fontSize: 12, color: 'var(--ink-3)' }}>继续追问…</span>
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>↵</span>
            </div>
          </Panel>

          {/* ◉ 右上：K 线 + 毛笔刻度（亮点） */}
          <Panel title="300750 · 宁德时代" sub="深圳 · 主板" tag={<span className="mono up" style={{ fontSize: 12, fontWeight: 600 }}>325.10 +2.21%</span>}>
            <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
              {/* 价格 + 标签条 */}
              <div style={{ display: 'flex', gap: 12, fontSize: 10, color: 'var(--ink-3)', fontFamily: 'var(--mono)', marginBottom: 4 }}>
                {['1D', '5D', '1M', '3M', '6M', '1Y'].map((p, i) => (
                  <span key={p} style={{ color: p === '1M' ? 'var(--ink)' : 'var(--ink-3)', fontWeight: p === '1M' ? 600 : 400, borderBottom: p === '1M' ? '1px solid var(--ink)' : 'none', paddingBottom: 1, cursor: 'pointer' }}>{p}</span>
                ))}
                <span style={{ flex: 1 }} />
                <span>MA5 322.4</span><span>MA10 318.6</span><span>MA20 308.2</span>
              </div>
              {/* K 线主图 */}
              <KCandleWithBrush data={candleData} w={612} h={196} />
              {/* 成交量 */}
              <VolumeBars data={candleData} w={612} h={48} />
            </div>
            {/* 关键比率行 */}
            <div style={{ marginTop: 8, display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', fontSize: 10, gap: 6 }} className="mono">
              {[
                { l: 'PE', v: '21.4' }, { l: 'PB', v: '4.6' }, { l: 'ROE', v: '24.6%' },
                { l: '股息率', v: '0.8%' }, { l: '总市值', v: '1.42万亿' }
              ].map((x, i) => (
                <div key={i} style={{ display: 'flex', flexDirection: 'column' }}>
                  <span style={{ fontSize: 9, color: 'var(--ink-3)' }}>{x.l}</span>
                  <span style={{ color: 'var(--ink)', fontWeight: 500, marginTop: 1 }}>{x.v}</span>
                </div>
              ))}
            </div>
          </Panel>

          {/* ◉ 左下：自选 / 持仓 */}
          <Panel title="自选" sub="6 只 · 实时" tag={<span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>＋ 添加</span>}>
            <div style={{ flex: 1, overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5, fontFamily: 'var(--mono)' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--ink-3)', fontSize: 10 }}>
                    <th style={{ textAlign: 'left', padding: '6px 0', fontWeight: 400 }}>名称</th>
                    <th style={{ textAlign: 'right', padding: '6px 0', fontWeight: 400 }}>现价</th>
                    <th style={{ textAlign: 'right', padding: '6px 0', fontWeight: 400 }}>涨跌</th>
                    <th style={{ textAlign: 'right', padding: '6px 0', fontWeight: 400 }}>趋势</th>
                    <th style={{ textAlign: 'right', padding: '6px 0', fontWeight: 400, paddingRight: 4 }}>AI</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    { n: '宁德时代', c: '300750', p: '325.10', d: '+2.21%', spark: [240,252,261,258,270,275,268,280,285,292,298,305,312,325], up: true, ai: '看多' },
                    { n: '贵州茅台', c: '600519', p: '1,684', d: '-0.42%', spark: [1720,1715,1690,1700,1685,1675,1690,1684], up: false, ai: '中性' },
                    { n: '比亚迪', c: '002594', p: '281.40', d: '+1.68%', spark: [262,258,265,268,272,275,278,281], up: true, ai: '看多' },
                    { n: '中际旭创', c: '300308', p: '142.55', d: '+4.12%', spark: [125,128,130,135,138,140,141,142], up: true, ai: '强势' },
                    { n: '隆基绿能', c: '601012', p: '17.84', d: '-1.06%', spark: [19.5,19.1,18.6,18.2,18.0,17.9,17.84], up: false, ai: '观望' },
                    { n: '招商银行', c: '600036', p: '37.92', d: '+0.32%', spark: [37.2,37.5,37.6,37.8,37.7,37.9,37.92], up: true, ai: '中性' },
                  ].map((r, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--line-soft)' }}>
                      <td style={{ padding: '8px 0' }}>
                        <div style={{ fontFamily: 'var(--serif)', fontSize: 12.5, color: 'var(--ink)' }}>{r.n}</div>
                        <div style={{ fontSize: 9, color: 'var(--ink-3)' }}>{r.c}</div>
                      </td>
                      <td style={{ textAlign: 'right', color: 'var(--ink)', fontWeight: 500 }}>{r.p}</td>
                      <td className={r.up ? 'up' : 'down'} style={{ textAlign: 'right', fontWeight: 500 }}>{r.d}</td>
                      <td style={{ textAlign: 'right', padding: '4px 0' }}>
                        <div style={{ display: 'inline-block' }}>
                          <Sparkline data={r.spark} w={70} h={20} up={r.up} fill={false} />
                        </div>
                      </td>
                      <td style={{ textAlign: 'right', paddingRight: 4 }}>
                        <span style={{
                          fontFamily: 'var(--serif)', fontSize: 11, padding: '1px 6px',
                          color: r.ai === '看多' || r.ai === '强势' ? 'var(--zhu)' : r.ai === '观望' || r.ai === '中性' ? 'var(--ink-2)' : 'var(--dai)',
                          border: '1px solid currentColor', borderColor: r.ai === '看多' || r.ai === '强势' ? 'var(--zhu)' : r.ai === '观望' || r.ai === '中性' ? 'var(--line)' : 'var(--dai)'
                        }}>{r.ai}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          {/* ◉ 右下：新闻 / 异动 流 */}
          <Panel title="实时" sub="新闻 · 公告 · 异动" tag={<span className="mono" style={{ fontSize: 10, color: 'var(--zhu)' }}>● LIVE</span>}>
            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: 0 }}>
              {[
                { t: '14:16', tag: 'AI 异动', tagC: 'yin', txt: '中际旭创 +4.12%，AI 监测到北向资金净买入 4.8 亿，触发"放量上攻"。', s: '相关：CPO、800G' },
                { t: '14:08', tag: '公告', tagC: 'ink', txt: '宁德时代：签订 19 GWh 海外储能订单，预计 2025 年起交付。', s: '300750.SZ · 巨潮' },
                { t: '13:54', tag: '研报', tagC: 'ink-2', txt: '中信证券：维持宁德时代"买入"评级，目标价 360 元。', s: '中信 · 谢家俊' },
                { t: '13:42', tag: '快讯', tagC: 'ink-2', txt: '10 月动力电池装车量公布：59.2 GWh，同比 +51%，环比 +12%。', s: '中汽协' },
                { t: '13:28', tag: '盘面', tagC: 'dai', txt: '锂电板块走弱：盛新锂能 -3.2%，天齐锂业 -2.1%，与上游碳酸锂回调有关。', s: '板块 · 锂矿' },
                { t: '13:11', tag: '互动', tagC: 'ink-2', txt: '比亚迪董秘回复："2025 年海外产能规划稳步推进。"', s: '深交所互动易' },
              ].map((n, i) => (
                <div key={i} style={{ display: 'flex', gap: 10, padding: '7px 0', borderBottom: i < 5 ? '1px solid var(--line-soft)' : 'none' }}>
                  <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', flexShrink: 0, marginTop: 2 }}>{n.t}</span>
                  <span style={{
                    fontFamily: 'var(--serif)', fontSize: 9.5, padding: '1px 5px', height: 16, flexShrink: 0, marginTop: 1,
                    background: n.tagC === 'yin' ? 'var(--yin)' : n.tagC === 'ink' ? 'var(--ink)' : 'transparent',
                    color: n.tagC === 'yin' || n.tagC === 'ink' ? 'var(--paper)' : n.tagC === 'dai' ? 'var(--dai)' : 'var(--ink-2)',
                    border: n.tagC === 'dai' ? '1px solid var(--dai)' : n.tagC === 'ink-2' ? '1px solid var(--line)' : 'none',
                    letterSpacing: '0.05em'
                  }}>{n.tag}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="serif" style={{ fontSize: 12.5, color: 'var(--ink)', lineHeight: 1.55 }}>{n.txt}</div>
                    <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>{n.s}</div>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
};

// ───── 通用 Panel ─────
const Panel = ({ title, sub, tag, children }) => (
  <section style={{ background: 'rgba(255,255,255,0.6)', border: '1px solid var(--line)', display: 'flex', flexDirection: 'column', padding: 14, minHeight: 0 }}>
    <header style={{ display: 'flex', alignItems: 'baseline', gap: 10, paddingBottom: 10, borderBottom: '1px solid var(--line-soft)', marginBottom: 12 }}>
      <span className="serif" style={{ fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{title}</span>
      {sub && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{sub}</span>}
      <span style={{ flex: 1 }} />
      {tag && (typeof tag === 'string'
        ? <span className="serif" style={{ fontSize: 11, padding: '1px 6px', background: 'var(--ink)', color: 'var(--paper)', letterSpacing: '0.08em' }}>{tag}</span>
        : tag)}
    </header>
    {children}
  </section>
);

// ───── K 线 with 毛笔风格刻度 ─────
const KCandleWithBrush = ({ data, w = 612, h = 196 }) => {
  const all = data.flatMap(d => [d.h, d.l]);
  const max = Math.max(...all), min = Math.min(...all);
  const padX = 4, padY = 8;
  const innerW = w - padX * 2 - 36; // 右侧留刻度
  const cw = innerW / data.length;
  const bw = cw * 0.6;
  const y = (v) => padY + ((max - v) / (max - min || 1)) * (h - padY * 2);

  const ticks = [max, (max * 0.66 + min * 0.34), (max * 0.33 + min * 0.67), min];

  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {/* 横向淡虚线 */}
      {ticks.map((t, i) => (
        <line key={i} x1={padX} x2={padX + innerW} y1={y(t)} y2={y(t)}
              stroke="var(--line-soft)" strokeDasharray="2 4" />
      ))}
      {/* 蜡烛 */}
      {data.map((d, i) => {
        const isUp = d.c >= d.o;
        const color = isUp ? 'var(--zhu)' : 'var(--dai)';
        const x = padX + i * cw + (cw - bw) / 2;
        const cx = padX + i * cw + cw / 2;
        const yo = y(d.o), yc = y(d.c), yh = y(d.h), yl = y(d.l);
        const top = Math.min(yo, yc), bh = Math.max(1, Math.abs(yo - yc));
        return (
          <g key={i}>
            <line x1={cx} x2={cx} y1={yh} y2={yl} stroke={color} strokeWidth="1" />
            <rect x={x} y={top} width={bw} height={bh} fill={color} />
          </g>
        );
      })}
      {/* 右侧毛笔刻度（亮点）：每个刻度由不规则笔触矩形组成 */}
      {ticks.map((t, i) => {
        const yy = y(t);
        return (
          <g key={'tk'+i} transform={`translate(${padX + innerW + 4}, ${yy})`}>
            {/* 笔触横线 */}
            <path d={`M0,0 C 4,-1 9,1 14,-0.5 L 14,0.5 C 9,1.5 4,2 0,1.2 Z`}
                  fill="var(--ink-1)" opacity="0.7" />
            <text x={20} y={4} fontFamily="var(--mono)" fontSize="9" fill="var(--ink-2)">{t.toFixed(0)}</text>
          </g>
        );
      })}
    </svg>
  );
};

const VolumeBars = ({ data, w = 612, h = 48 }) => {
  const padX = 4;
  const innerW = w - padX * 2 - 36;
  const cw = innerW / data.length;
  const bw = cw * 0.6;
  const vols = data.map(d => Math.abs(d.c - d.o) + (d.h - d.l) * 0.4 + 1);
  const max = Math.max(...vols);
  return (
    <svg width={w} height={h} style={{ display: 'block', marginTop: 4 }}>
      <line x1={padX} x2={padX + innerW} y1={h - 1} y2={h - 1} stroke="var(--line)" />
      {data.map((d, i) => {
        const isUp = d.c >= d.o;
        const color = isUp ? 'var(--zhu)' : 'var(--dai)';
        const x = padX + i * cw + (cw - bw) / 2;
        const bh = (vols[i] / max) * (h - 6);
        return <rect key={i} x={x} y={h - 1 - bh} width={bw} height={bh} fill={color} opacity={0.85} />;
      })}
    </svg>
  );
};

window.VariantPavilion = VariantPavilion;
