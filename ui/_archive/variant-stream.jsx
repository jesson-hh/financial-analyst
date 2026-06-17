// 甲 · 流 — 对话流变体（ChatGPT-like，AI 回复内联富媒体）
// 1440 x 900

const VariantStream = () => {
  const W = 1440, H = 900;

  // 示例数据
  const candleData = [
    {o: 240, c: 252, h: 256, l: 238},
    {o: 252, c: 248, h: 258, l: 245},
    {o: 248, c: 261, h: 264, l: 247},
    {o: 261, c: 258, h: 263, l: 254},
    {o: 258, c: 270, h: 274, l: 257},
    {o: 270, c: 275, h: 278, l: 266},
    {o: 275, c: 268, h: 277, l: 264},
    {o: 268, c: 280, h: 283, l: 267},
    {o: 280, c: 285, h: 288, l: 277},
    {o: 285, c: 281, h: 289, l: 278},
    {o: 281, c: 292, h: 295, l: 279},
    {o: 292, c: 298, h: 301, l: 290},
    {o: 298, c: 295, h: 302, l: 292},
    {o: 295, c: 305, h: 308, l: 293},
    {o: 305, c: 312, h: 316, l: 303},
  ];

  return (
    <div style={{ width: W, height: H, display: 'flex', fontFamily: 'var(--sans)', color: 'var(--ink)', background: 'var(--paper)' }} className="paper-bg">

      {/* ───── 左栏：任务历史 ───── */}
      <aside style={{ width: 252, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', background: 'rgba(241,234,217,0.5)' }}>
        <div style={{ padding: '22px 22px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Brandmark subtitle="GUAN LAN" small />
          <button style={{ width: 26, height: 26, border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', color: 'var(--ink-2)', fontSize: 14 }}>＋</button>
        </div>

        <div style={{ padding: '0 22px 14px' }}>
          <button style={{
            width: '100%', padding: '10px 14px', background: 'var(--ink)', color: 'var(--paper)',
            border: 'none', fontFamily: 'var(--serif)', fontSize: 13, letterSpacing: '0.06em', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8
          }}>
            <span>新任务</span>
            <span className="mono" style={{ fontSize: 10, opacity: 0.5 }}>⌘ N</span>
          </button>
        </div>

        <div style={{ padding: '8px 22px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em', textTransform: 'uppercase' }}>今日</div>
        {[
          { t: '宁德时代基本面分析', sub: '锂电板块景气度对比 · 进行中', active: true, badge: '研究中' },
          { t: '近一周北向资金流向', sub: '已完成 · 3 张图表' },
          { t: '消费板块复盘', sub: '已完成' },
        ].map((it, i) => (
          <div key={i} style={{
            padding: '12px 22px', borderLeft: it.active ? '2px solid var(--yin)' : '2px solid transparent',
            background: it.active ? 'rgba(168,57,45,0.06)' : 'transparent', cursor: 'pointer'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span className="serif" style={{ fontSize: 14, fontWeight: it.active ? 500 : 400, color: 'var(--ink)' }}>{it.t}</span>
              {it.badge && <span className="mono" style={{ fontSize: 9, color: 'var(--yin)', letterSpacing: '0.1em' }}>●</span>}
            </div>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 4 }}>{it.sub}</div>
          </div>
        ))}

        <div style={{ padding: '16px 22px 8px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em', textTransform: 'uppercase' }}>昨日</div>
        {[
          { t: '半导体国产替代逻辑', sub: '已完成' },
          { t: '某券商研报核查', sub: '已完成 · 6 处引用' },
          { t: '比亚迪 vs 长城财报对比', sub: '已完成' },
          { t: '今日异动股扫描', sub: '已完成 · 12 只' },
        ].map((it, i) => (
          <div key={i} style={{ padding: '10px 22px', cursor: 'pointer' }}>
            <div className="serif" style={{ fontSize: 13, color: 'var(--ink-1)' }}>{it.t}</div>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 3 }}>{it.sub}</div>
          </div>
        ))}

        <div style={{ flex: 1 }} />
        <div style={{ padding: '14px 22px', borderTop: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 26, height: 26, borderRadius: '50%', background: 'var(--ink-1)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 12 }}>陈</div>
          <div style={{ flex: 1, fontSize: 12 }}>
            <div style={{ color: 'var(--ink)' }}>陈先生</div>
            <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>本地模型 · Qwen-72B</div>
          </div>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--dai)' }} />
        </div>
      </aside>

      {/* ───── 主对话区 ───── */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>

        {/* 顶栏 */}
        <header style={{ padding: '18px 40px 16px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'rgba(241,234,217,0.4)' }}>
          <div>
            <div className="serif" style={{ fontSize: 17, color: 'var(--ink)', fontWeight: 500 }}>宁德时代基本面分析</div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3, letterSpacing: '0.08em' }}>
              CATL · 300750.SZ · 已运行 4 分 12 秒 · 引用 14 处
            </div>
          </div>
          <MarketTicker items={[
            { name: '上证', value: '3,287.42', delta: '+0.46%' },
            { name: '创业', value: '2,114.08', delta: '+1.12%' },
            { name: '沪深300', value: '3,892.51', delta: '+0.31%' },
            { name: '北向', value: '+38.4亿', delta: '+0.0%' },
          ]} />
        </header>

        {/* 对话区滚动 */}
        <div style={{ flex: 1, overflow: 'hidden', padding: '32px 40px 24px', display: 'flex', flexDirection: 'column', gap: 28 }}>

          {/* 用户提问 */}
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <div style={{
              maxWidth: '70%', padding: '14px 18px', background: 'var(--ink)',
              color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 14, lineHeight: 1.7
            }}>
              分析一下宁德时代最近的基本面变化，结合锂电池行业景气度，给我一个投资建议。
            </div>
          </div>

          {/* AI 研究步骤（亮点1：墨痕计时） */}
          <div style={{ display: 'flex', gap: 14 }}>
            <div style={{ width: 26, height: 26, flex: '0 0 26px', background: 'var(--paper-2)', border: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink)' }}>觀</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <details open style={{ background: 'rgba(255,255,255,0.5)', border: '1px solid var(--line-soft)' }}>
                <summary style={{ padding: '12px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span className="serif" style={{ fontSize: 13, color: 'var(--ink-1)' }}>研究计划 · 5 步</span>
                    <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>已完成 4 步 · 04:12</span>
                  </div>
                  <span style={{ color: 'var(--ink-3)', fontSize: 11 }}>收起 ▾</span>
                </summary>
                <div style={{ padding: '4px 16px 12px', borderTop: '1px solid var(--line-soft)' }}>
                  <ResearchStep step="01" label="拉取最近 4 个季度财报与一致预期" status="done" time="00:18" />
                  <ResearchStep step="02" label="抓取 8 月以来公告、互动易、券商研报" status="done" time="00:54" />
                  <ResearchStep step="03" label="对比锂电产业链景气数据：动力 / 储能 / 出货" status="done" time="02:30" />
                  <ResearchStep step="04" label="比较 同行（亿纬、国轩、中创新航）盈利结构" status="done" time="03:48" />
                  <ResearchStep step="05" label="撰写结论与风险提示" status="running" time="进行中" />
                </div>
              </details>
            </div>
          </div>

          {/* AI 主回复 */}
          <div style={{ display: 'flex', gap: 14 }}>
            <div style={{ width: 26, height: 26, flex: '0 0 26px', background: 'var(--paper-2)', border: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink)' }}>觀</div>
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>

              {/* 核心结论 */}
              <div style={{ fontFamily: 'var(--serif)', fontSize: 15, lineHeight: 1.85, color: 'var(--ink)' }}>
                <span className="serif" style={{ display: 'inline-block', padding: '2px 8px', background: 'var(--ink)', color: 'var(--paper)', fontSize: 11, letterSpacing: '0.1em', marginRight: 10, verticalAlign: 2 }}>结论</span>
                公司 Q3 业绩超一致预期，
                <span style={{ background: 'linear-gradient(180deg, transparent 60%, rgba(185,74,61,0.18) 60%)' }}>毛利率回升至 28.4%</span>
                ，储能业务占比首次突破 25%。但行业供给端仍处出清后期，建议
                <span style={{ background: 'linear-gradient(180deg, transparent 60%, rgba(74,107,92,0.18) 60%)' }}>分批建仓、关注 Q4 指引</span>。
              </div>

              {/* 关键数据卡（带边线） */}
              <div style={{ background: 'rgba(255,255,255,0.6)', border: '1px solid var(--line)', padding: '0 20px' }}>
                <div style={{ padding: '14px 0 10px', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', borderBottom: '1px solid var(--line-soft)' }}>
                  <span className="serif" style={{ fontSize: 13, fontWeight: 500 }}>关键财务（2024 Q3）</span>
                  <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>来源：东方财富 Choice · 巨潮 [3,4,7]</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)' }}>
                  <MetricCell label="营业收入" value="922.8" unit="亿" delta="+12.4% YoY" />
                  <MetricCell label="归母净利润" value="131.4" unit="亿" delta="+25.9% YoY" />
                  <MetricCell label="毛利率" value="28.4" unit="%" delta="+3.1 pct" />
                  <div style={{ padding: '14px 0', paddingRight: 0 }}>
                    <div style={{ fontSize: 11, color: 'var(--ink-2)', marginBottom: 6 }}>经营性现金流</div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                      <span className="mono" style={{ fontSize: 22, fontWeight: 500 }}>274.6</span>
                      <span style={{ fontSize: 11, color: 'var(--ink-2)' }}>亿</span>
                    </div>
                    <div className="up mono" style={{ fontSize: 11, marginTop: 4 }}>+38.2% YoY</div>
                  </div>
                </div>
              </div>

              {/* K 线 + 同业对比 */}
              <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 16 }}>
                <div style={{ background: 'rgba(255,255,255,0.6)', border: '1px solid var(--line)', padding: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
                    <span className="serif" style={{ fontSize: 13, fontWeight: 500 }}>近 15 个交易日</span>
                    <span className="mono up" style={{ fontSize: 13, fontWeight: 600 }}>312.40 +6.84%</span>
                  </div>
                  <Candles data={candleData} w={420} h={120} />
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }} className="mono" >
                    <span style={{ fontSize: 9, color: 'var(--ink-3)' }}>11/01</span>
                    <span style={{ fontSize: 9, color: 'var(--ink-3)' }}>11/22</span>
                  </div>
                </div>
                <div style={{ background: 'rgba(255,255,255,0.6)', border: '1px solid var(--line)', padding: 16 }}>
                  <div className="serif" style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>同业 · 毛利率</div>
                  {[
                    { n: '宁德时代', v: 28.4, pct: 95, focus: true },
                    { n: '亿纬锂能', v: 17.2, pct: 58 },
                    { n: '国轩高科', v: 14.6, pct: 49 },
                    { n: '中创新航', v: 11.8, pct: 39 },
                  ].map((r) => (
                    <div key={r.n} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 9 }}>
                      <span className="serif" style={{ fontSize: 12, width: 70, color: r.focus ? 'var(--ink)' : 'var(--ink-2)', fontWeight: r.focus ? 500 : 400 }}>{r.n}</span>
                      <div style={{ flex: 1, height: 4, background: 'var(--paper-2)' }}>
                        <div style={{ width: `${r.pct}%`, height: '100%', background: r.focus ? 'var(--yin)' : 'var(--ink-2)' }} />
                      </div>
                      <span className="mono" style={{ fontSize: 11, width: 38, textAlign: 'right' }}>{r.v}%</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* 风险提示 */}
              <div style={{ display: 'flex', gap: 12, padding: '12px 16px', borderLeft: '2px solid var(--dai)', background: 'rgba(74,107,92,0.06)' }}>
                <span className="serif" style={{ fontSize: 11, color: 'var(--dai)', letterSpacing: '0.1em', flexShrink: 0, marginTop: 2 }}>风险</span>
                <div style={{ fontFamily: 'var(--serif)', fontSize: 13, lineHeight: 1.75, color: 'var(--ink-1)' }}>
                  欧洲电动车需求放缓、海外建厂资本开支高峰、碳酸锂价格反弹挤压利润。
                  <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 6 }}>[9,11]</span>
                </div>
              </div>

              {/* 操作行 */}
              <div style={{ display: 'flex', gap: 18, marginTop: 4 }} className="mono">
                {[
                  { t: '导出 PDF', i: '↧' }, { t: '加入持仓监控', i: '✓' }, { t: '继续追问', i: '＋' }, { t: '查看引用 14', i: '※' },
                ].map((x, i) => (
                  <button key={i} style={{ background: 'transparent', border: 'none', color: 'var(--ink-2)', fontFamily: 'var(--mono)', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, padding: 0, letterSpacing: '0.05em' }}>
                    <span>{x.i}</span> <span>{x.t}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* 输入框 */}
        <div style={{ padding: '14px 40px 22px', borderTop: '1px solid var(--line-soft)', background: 'rgba(241,234,217,0.5)' }}>
          <div style={{ border: '1px solid var(--ink-2)', background: 'var(--paper)', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--ink-3)' }}>
              继续追问，或开启新任务…
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', gap: 8 }}>
                {['＠ 引用此股', '⌗ 选板块', '⚙ 模式：深度', '⊞ 上传文件'].map((x, i) => (
                  <span key={i} className="mono" style={{ fontSize: 10, color: 'var(--ink-2)', padding: '3px 8px', border: '1px solid var(--line)', cursor: 'pointer' }}>{x}</span>
                ))}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>本地 · Qwen-72B</span>
                <button style={{ background: 'var(--ink)', color: 'var(--paper)', border: 'none', padding: '6px 16px', fontFamily: 'var(--serif)', fontSize: 12, letterSpacing: '0.1em', cursor: 'pointer' }}>发送 ↵</button>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
};

window.VariantStream = VariantStream;
