// 甲 · 流 v2 — 针对 financial-analyst agent 的对话流主视图
// 1440 x 900
//
// 设计点（对应 FEATURES_FOR_DESIGN.md）：
// - 左栏：自选 / 盯盘（实时） / 历史
// - 顶栏：任务标题 + 权限模式 chip + 模型 + token + 盯盘状态 + 收盘倒计时
// - 中央：用户提问 → 多工具研究链（每个工具一张迷你卡片）→ 速览聚合卡 → LLM 总结（带印章引用）
// - 右栏：盯盘提醒抽屉 + 当前任务引用源
// - 底部：斜杠命令感知输入 + 状态行（模式/模型/token/auto-approved chips）

const StreamV2 = () => {
  const W = 1440, H = 1300;

  return (
    <div style={{ width: W, height: H, display: 'flex', fontFamily: 'var(--sans)', color: 'var(--ink)', background: 'var(--paper)' }} className="paper-bg">

      {/* ═════ 左栏 ═════ */}
      <LeftRail />

      {/* ═════ 中央对话区 ═════ */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, borderRight: '1px solid var(--line)' }}>
        <TopBar />
        <Transcript />
        <Composer />
        <StatusBar />
      </main>

      {/* ═════ 右栏：盯盘 + 引用 ═════ */}
      <RightRail />
    </div>
  );
};

// ───── 左栏 ─────
const LeftRail = () => (
  <aside style={{ width: 248, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', flexShrink: 0, background: 'rgba(241,234,217,0.55)' }}>
    {/* 品牌 + 新任务 */}
    <div style={{ padding: '20px 20px 14px' }}>
      <Brandmark subtitle="A 股 AI 助手" small />
    </div>
    <div style={{ padding: '0 20px 14px' }}>
      <button style={{
        width: '100%', padding: '9px 12px', background: 'var(--ink)', color: 'var(--paper)',
        border: 'none', fontFamily: 'var(--serif)', fontSize: 13, letterSpacing: '0.06em', cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between'
      }}>
        <span>＋ 新对话</span>
        <span className="mono" style={{ fontSize: 10, opacity: 0.5 }}>⌘ N</span>
      </button>
    </div>

    {/* 自选 (mini) */}
    <RailSection label="自选" right={<span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>6</span>}>
      {[
        { n: '宁德时代', c: '300750', p: '325.10', d: '+2.21', spark: [240,252,261,270,275,280,292,305,312,325], up: true },
        { n: '贵州茅台', c: '600519', p: '1,684', d: '-0.42', spark: [1720,1690,1700,1685,1690,1684], up: false },
        { n: '比亚迪',   c: '002594', p: '281.40', d: '+1.68', spark: [262,265,268,272,275,278,281], up: true },
        { n: '中际旭创', c: '300308', p: '142.55', d: '+4.12', spark: [125,128,135,140,142], up: true },
      ].map((r, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 20px', cursor: 'pointer' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)' }}>{r.n}</div>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{r.c}</div>
          </div>
          <div style={{ flexShrink: 0 }}>
            <Sparkline data={r.spark} w={42} h={14} up={r.up} fill={false} />
          </div>
          <div style={{ flexShrink: 0, textAlign: 'right', width: 52 }}>
            <div className="mono" style={{ fontSize: 11, color: 'var(--ink)' }}>{r.p}</div>
            <div className={'mono ' + (r.up ? 'up' : 'down')} style={{ fontSize: 9 }}>{r.d}%</div>
          </div>
        </div>
      ))}
    </RailSection>

    {/* 盯盘 alerts */}
    <RailSection label="盯盘" right={<span className="mono" style={{ fontSize: 10, color: 'var(--yin)' }}>● 3 活跃</span>}>
      {[
        { n: '贵州茅台', rule: '跌破 1,200', cur: '1,684', d: 'price_below' },
        { n: '宁德时代', rule: '涨 5% 以上', cur: '+2.21%', d: 'pct_above', hot: true },
        { n: '比亚迪',   rule: '涨破 300', cur: '281.40', d: 'price_above' },
      ].map((a, i) => (
        <div key={i} style={{ padding: '7px 20px', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 6, height: 6, background: a.hot ? 'var(--yin)' : 'var(--ink-3)', flexShrink: 0, borderRadius: a.hot ? 0 : '50%' }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)' }}>{a.n}</div>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{a.rule}</div>
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>{a.cur}</div>
        </div>
      ))}
    </RailSection>

    {/* 历史 */}
    <RailSection label="今日历史" >
      {[
        { t: '看下宁德时代怎么样', s: '进行中', active: true },
        { t: '今天主力在买什么', s: '04:18 · 完成' },
        { t: 'CPO 板块还能不能追', s: '02:31 · 完成' },
        { t: '茅台跌破1200提醒我', s: '08:14 · 已设盯盘' },
      ].map((h, i) => (
        <div key={i} style={{
          padding: '7px 20px', cursor: 'pointer',
          borderLeft: h.active ? '2px solid var(--yin)' : '2px solid transparent',
          background: h.active ? 'rgba(168,57,45,0.05)' : 'transparent',
          marginLeft: h.active ? 0 : 0,
        }}>
          <div className="serif" style={{ fontSize: 12.5, color: h.active ? 'var(--ink)' : 'var(--ink-1)', fontWeight: h.active ? 500 : 400 }}>{h.t}</div>
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>{h.s}</div>
        </div>
      ))}
    </RailSection>

    <div style={{ flex: 1 }} />

    {/* 工具面板入口 */}
    <div style={{ padding: '12px 20px', borderTop: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
      <span style={{ width: 22, height: 22, border: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink-2)' }}>⌘</span>
      <span className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', flex: 1 }}>工具面板 · 26 个</span>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>⌘ K</span>
    </div>
  </aside>
);

const RailSection = ({ label, right, children }) => (
  <div style={{ paddingTop: 6, paddingBottom: 8 }}>
    <div style={{ display: 'flex', alignItems: 'center', padding: '4px 20px 6px' }}>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.2em', textTransform: 'uppercase', flex: 1 }}>{label}</span>
      {right}
    </div>
    {children}
  </div>
);

// ───── 顶栏 ─────
const TopBar = () => (
  <header style={{ padding: '14px 32px 12px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0, background: 'rgba(241,234,217,0.4)' }}>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div className="serif" style={{ fontSize: 16, color: 'var(--ink)', fontWeight: 500 }}>看下宁德时代怎么样</div>
      <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3, letterSpacing: '0.05em' }}>
        300750.SZ · stock_brief · 已执行 5 个工具 · 用时 38 秒
      </div>
    </div>
    {/* 实时大盘 mini */}
    <div style={{ display: 'flex', gap: 18, fontFamily: 'var(--mono)', fontSize: 11 }}>
      {[{n:'上证',v:'3,287',d:'+0.46'},{n:'深成',v:'10,524',d:'+0.82'},{n:'创业',v:'2,114',d:'+1.12'}].map((x,i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
          <span style={{ color: 'var(--ink-3)', fontSize: 10 }}>{x.n}</span>
          <span style={{ color: 'var(--ink-1)' }}>{x.v}</span>
          <span className={x.d.startsWith('-') ? 'down' : 'up'}>{x.d}%</span>
        </div>
      ))}
    </div>
    <div style={{ width: 1, height: 22, background: 'var(--line)' }} />
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }} className="mono">
      <span style={{ width: 5, height: 5, background: 'var(--zhu)', borderRadius: '50%' }} />
      <span style={{ fontSize: 10, color: 'var(--ink-2)' }}>交易中 · 14:17</span>
      <span style={{ fontSize: 10, color: 'var(--ink-3)' }}>·</span>
      <span style={{ fontSize: 10, color: 'var(--ink-3)' }}>距收盘 00:42:17</span>
    </div>
  </header>
);

// ───── 主对话 ─────
const Transcript = () => (
  <div style={{ flex: 1, overflow: 'visible', padding: '20px 56px 24px', display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0 }}>

    {/* 用户提问 */}
    <UserBubble text="看下宁德时代怎么样" />

    {/* 工具链 ribbon */}
    <ToolChain />

    {/* 速览聚合卡 — stock_brief 标志性输出 */}
    <StockBriefCard />

    {/* LLM 总结 + 印章引用 */}
    <AiSummary />
  </div>
);

const UserBubble = ({ text }) => (
  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
    <div style={{
      maxWidth: '72%', padding: '12px 16px', background: 'var(--ink)',
      color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 14, lineHeight: 1.65
    }}>{text}</div>
  </div>
);

// 工具链 — 5 个工具调用以"研究纸带"竖排呈现
const ToolChain = () => {
  const tools = [
    { i: 1, name: 'realtime_quote',  cn: '抓取实时行情', args: '{ symbol: "300750" }', t: '0.4s', status: 'done', result: '325.10 +2.21% · 量比 1.42 · 换手 2.18%' },
    { i: 2, name: 'ths_fund_flow',   cn: '同花顺资金流', args: '{ target: "stock", symbol: "300750" }', t: '2.8s', status: 'done', result: '主力净流入 4.82 亿 · 大单 +3.1 亿 · 中单 +1.7 亿' },
    { i: 3, name: 'news_query',      cn: '本地新闻全文检索', args: '{ keyword: "宁德时代", days: 7 }', t: '0.2s', status: 'done', result: '14 条 · 含巨潮公告 2 · 雪球热门 5 · 东方财富快讯 7' },
    { i: 4, name: 'chain_for',       cn: '产业链上下游', args: '{ symbol: "300750" }', t: '0.1s', status: 'done', result: '上游：锂矿/正负极/电解液（6） · 同行：5 · 下游：整车厂 8', cite: true },
    { i: 5, name: 'stocks_show',     cn: '历史研究时间线', args: '{ symbol: "300750", limit: 3 }', t: '0.3s', status: 'running', result: '查询中…' },
  ];
  return (
    <div style={{ display: 'flex', gap: 14 }}>
      <AiAvatar />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ background: 'rgba(255,255,255,0.5)', border: '1px solid var(--line-soft)' }}>
          <div style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--line-soft)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span className="serif" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>研究链 · stock_brief</span>
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>5 个工具 · 04 / 05 · 已用 4.2s</span>
            </div>
            <div style={{ display: 'flex', gap: 10 }} className="mono">
              <span style={{ fontSize: 10, color: 'var(--ink-3)' }}>展开全部 ▾</span>
              <span style={{ fontSize: 10, color: 'var(--ink-2)' }}>ESC 取消</span>
            </div>
          </div>
          <div style={{ padding: '4px 0' }}>
            {tools.map((tl, i) => (
              <ToolRow key={i} {...tl} last={i === tools.length - 1} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

const ToolRow = ({ i, name, cn, args, t, status, result, last, cite }) => {
  const running = status === 'running';
  return (
    <div style={{ display: 'flex', gap: 14, padding: '8px 16px', alignItems: 'flex-start', position: 'relative' }}>
      {/* 印章序号 */}
      <div style={{ position: 'relative', width: 22, flex: '0 0 22px' }}>
        <div style={{
          width: 22, height: 22, background: running ? 'var(--yin)' : 'var(--ink)', color: 'var(--paper)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'var(--serif)', fontSize: 11, fontWeight: 500
        }}>{i}</div>
        {!last && <div style={{ position: 'absolute', top: 24, left: 10, bottom: -12, width: 2, background: 'var(--line)' }} />}
        {running && (
          <div style={{ position: 'absolute', inset: -3, width: 28, height: 28, border: '1px solid var(--yin)', opacity: 0.4, animation: 'pulse 1.6s ease-in-out infinite' }} />
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <code style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)', fontWeight: 500 }}>{name}</code>
          <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{cn}</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', whiteSpace: 'nowrap', marginLeft: 'auto' }}>
            {running ? '⠋ 运行中…' : `✓ ${t}`}
          </span>
        </div>
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2 }}>{args}</div>
        <div className="serif" style={{ fontSize: 12.5, color: running ? 'var(--ink-3)' : 'var(--ink-1)', marginTop: 5, fontStyle: running ? 'italic' : 'normal', display: 'flex', alignItems: 'center', gap: 6 }}>
          {!running && <span style={{ color: 'var(--ink-3)' }}>→</span>}
          <span style={{ flex: 1 }}>{result}</span>
          {cite && <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 16, height: 16, background: 'var(--paper-2)', border: '1px solid var(--line)', color: 'var(--ink-2)', fontFamily: 'var(--mono)', fontSize: 9, cursor: 'pointer' }}>↗</span>}
        </div>
      </div>
    </div>
  );
};

const AiAvatar = () => (
  <div style={{ width: 28, height: 28, flex: '0 0 28px', background: 'var(--paper-2)', border: '1px solid var(--ink)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--ink)' }}>觀</div>
);

// 速览聚合卡 — stock_brief 的标志性聚合输出
const StockBriefCard = () => (
  <div style={{ display: 'flex', gap: 14 }}>
    <AiAvatar />
    <div style={{ flex: 1, minWidth: 0, background: 'var(--paper)', border: '1px solid var(--ink)' }}>
      {/* 头 */}
      <div style={{ padding: '14px 18px 12px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', borderBottom: '1px solid var(--ink)' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
            <span className="serif" style={{ fontSize: 22, fontWeight: 500, color: 'var(--ink)' }}>宁德时代</span>
            <span className="mono" style={{ fontSize: 12, color: 'var(--ink-3)' }}>300750.SZ · 深主板</span>
            <span className="serif" style={{ fontSize: 11, padding: '1px 6px', background: 'var(--ink)', color: 'var(--paper)', letterSpacing: '0.08em' }}>速览</span>
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 4 }}>
            申万 · 电力设备 / 电池 · 总市值 14,206 亿
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="mono up" style={{ fontSize: 24, fontWeight: 500 }}>325.10</div>
          <div className="mono up" style={{ fontSize: 12 }}>+7.04 (+2.21%)</div>
        </div>
      </div>

      {/* 4 区聚合 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid var(--line)' }}>
        {/* 行情 + 估值 */}
        <BriefRegion title="行情 / 估值" cite="1">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
            {[
              { l: '量比', v: '1.42' }, { l: '换手', v: '2.18%' }, { l: '振幅', v: '3.84%' },
              { l: 'PE TTM', v: '21.4' }, { l: 'PB', v: '4.6' }, { l: 'ROE', v: '24.6%' },
            ].map((m, i) => (
              <div key={i}>
                <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>{m.l}</div>
                <div className="mono" style={{ fontSize: 13, color: 'var(--ink)' }}>{m.v}</div>
              </div>
            ))}
          </div>
        </BriefRegion>

        {/* 资金流 */}
        <BriefRegion title="主力资金 · 今日" cite="2" borderL>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
            <span className="mono up" style={{ fontSize: 18, fontWeight: 500 }}>+4.82</span>
            <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)' }}>亿 净流入</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 'auto' }}>vs 昨 +1.9</span>
          </div>
          {/* 4 档资金条 */}
          {[
            { l: '超大单', v: '+2.1', pct: 70, up: true },
            { l: '大单',   v: '+1.0', pct: 33, up: true },
            { l: '中单',   v: '+1.7', pct: 56, up: true },
            { l: '小单',   v: '-0.4', pct: 13, up: false },
          ].map((b, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', width: 38 }}>{b.l}</span>
              <div style={{ flex: 1, height: 3, background: 'var(--paper-2)', position: 'relative' }}>
                <div style={{ width: `${b.pct}%`, height: '100%', background: b.up ? 'var(--zhu)' : 'var(--dai)' }} />
              </div>
              <span className={'mono ' + (b.up ? 'up' : 'down')} style={{ fontSize: 11, width: 36, textAlign: 'right' }}>{b.v}亿</span>
            </div>
          ))}
        </BriefRegion>

        {/* 新闻摘要 */}
        <BriefRegion title="最近 7 日新闻 · 14 条" cite="3" borderT>
          {[
            { t: '宁德时代签订 19 GWh 海外储能订单', s: '巨潮 · 公告 · 11-04', tone: 'up' },
            { t: '中信维持"买入"评级 目标价 360', s: '中信证券研报 · 11-12', tone: 'up' },
            { t: '10 月动力电池装车量 +51% YoY', s: '中汽协 · 11-12', tone: 'up' },
          ].map((n, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4px 0', borderTop: i ? '1px dashed var(--line-soft)' : 'none' }}>
              <span style={{ width: 4, height: 4, background: n.tone === 'up' ? 'var(--zhu)' : 'var(--dai)', flexShrink: 0, marginTop: 6 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.5 }}>{n.t}</div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{n.s}</div>
              </div>
            </div>
          ))}
        </BriefRegion>

        {/* 产业链 + 情绪 */}
        <BriefRegion title="产业链 / 雪球情绪" cite="4" borderL borderT>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginBottom: 4 }}>同行（毛利率）</div>
          {[
            { n: '宁德时代', v: 28.4, pct: 95, focus: true },
            { n: '亿纬锂能', v: 17.2, pct: 58 },
            { n: '中创新航', v: 11.8, pct: 39 },
          ].map((r) => (
            <div key={r.n} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
              <span className="serif" style={{ fontSize: 11, width: 56, color: r.focus ? 'var(--ink)' : 'var(--ink-2)' }}>{r.n}</span>
              <div style={{ flex: 1, height: 3, background: 'var(--paper-2)' }}>
                <div style={{ width: `${r.pct}%`, height: '100%', background: r.focus ? 'var(--yin)' : 'var(--ink-2)' }} />
              </div>
              <span className="mono" style={{ fontSize: 10, width: 30, textAlign: 'right' }}>{r.v}%</span>
            </div>
          ))}
          <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px dashed var(--line-soft)', display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>雪球情绪</span>
            <div style={{ flex: 1, height: 4, background: 'var(--paper-2)', position: 'relative' }}>
              <div style={{ width: '68%', height: '100%', background: 'var(--zhu)' }} />
            </div>
            <span className="mono up" style={{ fontSize: 11 }}>偏多 68%</span>
          </div>
        </BriefRegion>
      </div>

      {/* 操作行 */}
      <div style={{ padding: '8px 18px', display: 'flex', gap: 16, fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-2)', background: 'rgba(241,234,217,0.4)' }}>
        <span style={{ cursor: 'pointer' }}>↧ 导出 markdown</span>
        <span style={{ cursor: 'pointer' }}>＋ 加入自选</span>
        <span style={{ cursor: 'pointer' }}>👁 添加盯盘…</span>
        <span style={{ cursor: 'pointer' }}>⊟ 跑深度研报…（约 6 分钟）</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: 'var(--ink-3)' }}>引用 1·2·3·4 · 来源 7 处</span>
      </div>
    </div>
  </div>
);

const BriefRegion = ({ title, cite, children, borderL, borderT }) => (
  <div style={{ padding: '12px 16px', borderLeft: borderL ? '1px solid var(--line)' : 'none', borderTop: borderT ? '1px solid var(--line)' : 'none' }}>
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
      <span className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', fontWeight: 500 }}>{title}</span>
      {cite && <sup style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 15, height: 15, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 9, cursor: 'pointer' }}>{cite}</sup>}
    </div>
    {children}
  </div>
);

// LLM 总结 + 印章引用
const AiSummary = () => (
  <div style={{ display: 'flex', gap: 14 }}>
    <AiAvatar />
    <div style={{ flex: 1, minWidth: 0, fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--ink)', lineHeight: 1.9, padding: '4px 0' }}>
      宁德 Q3 利润同比 <span className="up mono" style={{ fontWeight: 500 }}>+25.9%</span> 超预期，毛利率回升至 28.4%<Cite n="1"/>，
      今日主力净流入 <span className="up mono" style={{ fontWeight: 500 }}>4.82 亿</span>（连续 3 日加仓）<Cite n="2"/>。
      消息面有 <strong style={{ fontWeight: 600 }}>19 GWh 海外储能订单</strong><Cite n="3"/> 与中信 360 目标价催化，
      同行毛利率对比仍处第一梯队<Cite n="4"/>。短线情绪偏多，建议
      <span style={{ background: 'linear-gradient(180deg, transparent 60%, rgba(185,74,61,0.18) 60%)' }}>分批跟进，关注 330 一线压力</span>。
      <span className="mono" style={{ display: 'inline-block', marginLeft: 8, padding: '1px 6px', background: 'rgba(0,0,0,0.04)', fontSize: 10, color: 'var(--ink-3)' }}>正在生成…</span>
    </div>
  </div>
);

const Cite = ({ n }) => (
  <sup style={{
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    width: 14, height: 14, background: 'var(--yin)', color: 'var(--paper)',
    fontFamily: 'var(--serif)', fontSize: 8, fontWeight: 500,
    margin: '0 1px', verticalAlign: 2, cursor: 'pointer'
  }}>{n}</sup>
);

// ───── 输入框 ─────
const Composer = () => (
  <div style={{ padding: '14px 56px 8px', flexShrink: 0 }}>
    <div style={{ border: '1px solid var(--ink-2)', background: 'var(--paper)' }}>
      {/* slash 提示行 */}
      <div style={{ padding: '6px 14px', borderBottom: '1px dashed var(--line)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>提示</span>
        {['/ 命令', '@ 引用此股', '⌗ 板块', '↑ 上一题', '⌘K 工具面板'].map((x, i) => (
          <span key={i} className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>{x}</span>
        ))}
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>盘中 · 优先选用实时工具</span>
      </div>
      <div style={{ padding: '12px 14px 10px' }}>
        <div className="serif" style={{ fontSize: 14, color: 'var(--ink-3)' }}>
          继续追问，或开启新任务…
        </div>
      </div>
      <div style={{ padding: '6px 14px 8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 6 }} className="mono">
          {['⊟ 上传', '@ 引用', '⌗ 板块'].map((x, i) => (
            <span key={i} style={{ fontSize: 10, color: 'var(--ink-2)', padding: '3px 7px', border: '1px solid var(--line)', cursor: 'pointer' }}>{x}</span>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>本地 · Qwen3.5-plus</span>
          <button style={{ background: 'var(--ink)', color: 'var(--paper)', border: 'none', padding: '5px 14px', fontFamily: 'var(--serif)', fontSize: 12, letterSpacing: '0.1em', cursor: 'pointer' }}>发送 ↵</button>
        </div>
      </div>
    </div>
  </div>
);

// ───── 底部状态行 ─────
const StatusBar = () => (
  <footer style={{ padding: '6px 32px 8px', borderTop: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 14, fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-2)', flexShrink: 0 }}>
    {/* 权限模式 */}
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '2px 7px', background: 'rgba(74,107,92,0.12)', color: 'var(--dai)' }}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--dai)' }} />
      <span style={{ fontWeight: 500, letterSpacing: '0.05em' }}>default</span>
    </span>
    {/* 模型 */}
    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span style={{ color: 'var(--ink-3)' }}>模型</span>
      <span style={{ color: 'var(--ink-1)' }}>qwen3.5-plus</span>
    </span>
    <Sep />
    {/* token */}
    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span style={{ color: 'var(--ink-3)' }}>token</span>
      <span style={{ color: 'var(--ink-1)' }}>2.3k</span>
      <span style={{ color: 'var(--ink-3)' }}>(↑1,800 ↓500 · 4 calls)</span>
    </span>
    <Sep />
    {/* watch */}
    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span style={{ color: 'var(--ink-3)' }}>盯盘</span>
      <span style={{ color: 'var(--ink-1)' }}>5 分钟</span>
      <span style={{ color: 'var(--zhu)' }}>· 交易中</span>
    </span>
    <Sep />
    {/* auto-approved */}
    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span style={{ color: 'var(--ink-3)' }}>已免确认</span>
      <span style={{ padding: '1px 5px', border: '1px solid var(--line)', color: 'var(--ink-1)', cursor: 'pointer' }}>news_query ×</span>
      <span style={{ padding: '1px 5px', border: '1px solid var(--line)', color: 'var(--ink-1)', cursor: 'pointer' }}>quote_lookup ×</span>
    </span>
    <span style={{ flex: 1 }} />
    <span style={{ color: 'var(--ink-3)' }}>v1.8.3</span>
  </footer>
);

const Sep = () => <span style={{ color: 'var(--ink-3)' }}>·</span>;

// ───── 右栏：盯盘抽屉 + 引用源 ─────
const RightRail = () => (
  <aside style={{ width: 312, display: 'flex', flexDirection: 'column', flexShrink: 0, background: 'rgba(241,234,217,0.4)' }}>
    {/* 抽屉头 */}
    <div style={{ padding: '14px 18px 10px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 10 }}>
      <span className="serif" style={{ fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>盯盘</span>
      <span className="mono" style={{ fontSize: 10, color: 'var(--yin)' }}>● 后台运行</span>
      <span style={{ flex: 1 }} />
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', cursor: 'pointer' }}>/watch ▾</span>
    </div>

    {/* 最近触发 — 印章红警铃卡 */}
    <div style={{ padding: '12px 18px 8px' }}>
      <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.2em', marginBottom: 8 }}>最近触发</div>
      <div style={{ border: '1px solid var(--yin)', padding: '10px 12px', background: 'rgba(168,57,45,0.06)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <span style={{ width: 18, height: 18, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 11, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>🔔</span>
          <span className="serif" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>中际旭创</span>
          <span className="mono up" style={{ fontSize: 11, fontWeight: 500, marginLeft: 'auto' }}>+4.12%</span>
        </div>
        <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.5 }}>
          触发规则 <span className="mono">pct_above 4%</span>
        </div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 6 }}>142.55 · 13:54 · 量比 2.1</div>
        <div style={{ display: 'flex', gap: 6, marginTop: 8 }} className="mono">
          <span style={{ fontSize: 10, color: 'var(--ink-2)', padding: '2px 6px', border: '1px solid var(--line)', cursor: 'pointer' }}>追问 →</span>
          <span style={{ fontSize: 10, color: 'var(--ink-2)', padding: '2px 6px', border: '1px solid var(--line)', cursor: 'pointer' }}>暂停规则</span>
        </div>
      </div>
    </div>

    {/* 活跃规则 */}
    <div style={{ padding: '8px 18px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.2em' }}>活跃规则 · 3</span>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)', cursor: 'pointer' }}>+ 添加</span>
      </div>
      {[
        { n: '贵州茅台', rule: '价跌破 1,200', cur: '1,684', pct: 28, far: '+40.3%' },
        { n: '宁德时代', rule: '日涨 ≥ 5%', cur: '+2.21%', pct: 44, far: '剩 2.79 pct' },
        { n: '比亚迪',   rule: '价涨破 300',   cur: '281.40', pct: 92, far: '剩 6.62%' },
      ].map((a, i) => (
        <div key={i} style={{ padding: '10px 0', borderTop: i ? '1px solid var(--line-soft)' : 'none' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 4 }}>
            <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink)' }}>{a.n}</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{a.rule}</span>
            <span style={{ flex: 1 }} />
            <span className="mono" style={{ fontSize: 11, color: 'var(--ink-1)' }}>{a.cur}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ flex: 1, height: 2, background: 'var(--paper-2)' }}>
              <div style={{ width: `${a.pct}%`, height: '100%', background: 'var(--ink-1)' }} />
            </div>
            <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{a.far}</span>
          </div>
        </div>
      ))}
    </div>

    {/* 当前任务引用源 */}
    <div style={{ padding: '14px 18px 10px', borderTop: '1px solid var(--line)' }}>
      <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.2em', marginBottom: 10 }}>当前任务引用 · 4</div>
      {[
        { n: 1, src: 'realtime_quote', t: '同花顺 · 14:17', d: '325.10 +2.21%' },
        { n: 2, src: 'ths_fund_flow',  t: '同花顺 · 14:17', d: '主力净流入 4.82 亿' },
        { n: 3, src: 'news_query',     t: '本地 SQLite · 7d', d: '14 条新闻 · 巨潮+中信+中汽协' },
        { n: 4, src: 'chain_for',      t: '产业链库',         d: '同业 5 · 上游 6 · 下游 8' },
      ].map((c, i) => (
        <div key={i} style={{ display: 'flex', gap: 10, padding: '8px 0', borderTop: i ? '1px solid var(--line-soft)' : 'none' }}>
          <span style={{ width: 18, height: 18, background: 'var(--yin)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 10, flexShrink: 0 }}>{c.n}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <code style={{ fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink-1)' }}>{c.src}</code>
            <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-2)', marginTop: 1 }}>{c.d}</div>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 1 }}>{c.t}</div>
          </div>
        </div>
      ))}
    </div>

    <div style={{ flex: 1 }} />

    {/* 数据源时效守卫 */}
    <div style={{ padding: '10px 18px', borderTop: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--dai)' }} />
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>所有数据源 ≤ 4 分钟</span>
      <span style={{ flex: 1 }} />
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', cursor: 'pointer' }}>刷新 ↻</span>
    </div>
  </aside>
);

window.StreamV2 = StreamV2;
