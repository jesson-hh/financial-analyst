// 观澜 · 量化研究 — single bundled JSX

const { useState, useMemo, useRef, useEffect, useCallback } = React;

// ───────── 因子库 (mock) ─────────
const LIBS = [
  { id: 'alpha101', name: 'Alpha 101',  src: 'WorldQuant Formulaic Alphas', count: 101 },
  { id: 'gtja191',  name: 'GTJA 191',   src: '国泰君安 191 短周期因子',     count: 191 },
  { id: 'qlib158',  name: 'Qlib Alpha158', src: 'Microsoft Qlib 基础因子', count: 158 },
];

// 价量/动量/反转/波动率/盈利/资金流 几大类
const FACTORS = [
  // alpha101
  { id: 'alpha#6',  lib: 'alpha101', cat: '价量', icm: -0.038, icir: -1.42, sharpe: 1.21, ar: 14.2, mdd: -8.4, turn: 142, hot: false,
    formula: '(-1 * correlation(open, volume, 10))' },
  { id: 'alpha#14', lib: 'alpha101', cat: '动量', icm: -0.029, icir: -1.18, sharpe: 0.94, ar: 11.8, mdd: -10.1, turn: 156, hot: false,
    formula: '((-1 * rank(delta(returns, 3))) * correlation(open, volume, 10))' },
  { id: 'alpha#41', lib: 'alpha101', cat: '价量', icm: -0.046, icir: -1.82, sharpe: 1.47, ar: 18.6, mdd: -7.2, turn: 168, hot: true,
    formula: '(((high * low)^0.5) - vwap)' },
  { id: 'alpha#54', lib: 'alpha101', cat: '反转', icm:  0.034, icir:  1.56, sharpe: 1.32, ar: 16.4, mdd: -8.8, turn: 132, hot: false,
    formula: '((-1 * ((low - close) * (open^5))) / ((low - high) * (close^5)))' },
  { id: 'alpha#94', lib: 'alpha101', cat: '动量', icm:  0.041, icir:  1.71, sharpe: 1.38, ar: 17.2, mdd: -9.6, turn: 124, hot: false,
    formula: '((rank((vwap - ts_min(vwap, 12)))^Ts_Rank(correlation(...), 3)) * -1)' },
  { id: 'alpha#101',lib: 'alpha101', cat: '价量', icm:  0.022, icir:  0.94, sharpe: 0.71, ar:  9.6, mdd: -11.2, turn: 98,  hot: false,
    formula: '((close - open) / ((high - low) + 0.001))' },
  // gtja191
  { id: 'gtja#3',   lib: 'gtja191',  cat: '反转', icm:  0.052, icir:  2.04, sharpe: 1.62, ar: 21.4, mdd: -6.8, turn: 184, hot: true,
    formula: 'SUM((CLOSE - DELAY(CLOSE,1)) > 0 ? CLOSE - MIN(LOW, DELAY(CLOSE,1)) : ..., 6)' },
  { id: 'gtja#34',  lib: 'gtja191',  cat: '动量', icm:  0.036, icir:  1.42, sharpe: 1.18, ar: 13.4, mdd: -9.4, turn: 116, hot: false,
    formula: 'MEAN(CLOSE, 12) / CLOSE' },
  { id: 'gtja#88',  lib: 'gtja191',  cat: '波动', icm: -0.031, icir: -1.24, sharpe: 0.96, ar: 10.8, mdd: -12.4, turn: 88,  hot: false,
    formula: '(CLOSE - DELAY(CLOSE, 20)) / DELAY(CLOSE, 20) * 100' },
  // qlib158
  { id: 'qlib#114', lib: 'qlib158',  cat: '盈利', icm:  0.058, icir:  2.31, sharpe: 1.78, ar: 23.8, mdd: -5.6, turn: 64,  hot: true,
    formula: 'ROE_TTM · 一致预期 ROE 上修 + 实际值环比改善' },
  { id: 'qlib#42',  lib: 'qlib158',  cat: '资金', icm:  0.044, icir:  1.84, sharpe: 1.42, ar: 18.2, mdd: -7.8, turn: 142, hot: false,
    formula: 'KMID — (close - open) / open · 价量 K 线中位' },
  { id: 'qlib#77',  lib: 'qlib158',  cat: '价量', icm:  0.028, icir:  1.12, sharpe: 0.88, ar: 11.2, mdd: -10.6, turn: 122, hot: false,
    formula: 'CORR(close, volume, 20) — 量价相关 20 日' },
];

// 因子 IC 序列 (mock 24 个月)
function genICSeries(seed = 0) {
  const base = Math.sin(seed) * 0.02;
  const arr = [];
  let v = base;
  for (let i = 0; i < 24; i++) {
    v = base + Math.sin((i + seed) * 0.6) * 0.04 + (Math.cos((i+seed)*1.3))*0.015;
    arr.push(+v.toFixed(4));
  }
  return arr;
}
function genEquity(seed = 0) {
  const arr = [1.0];
  let v = 1.0;
  for (let i = 0; i < 252; i++) {
    const r = Math.sin((i+seed)*0.07)*0.003 + Math.sin((i+seed)*0.21)*0.002 + 0.0007;
    v = v * (1 + r);
    arr.push(+v.toFixed(4));
  }
  return arr;
}
function genDecile(seed = 0) {
  // 10 个十分位的年化超额 (%)
  const arr = [];
  for (let i = 0; i < 10; i++) {
    arr.push(+(((i - 4.5) * 2.2) + Math.sin((i+seed)*1.7)*1.8).toFixed(2));
  }
  return arr;
}
function genPicks(seed = 0) {
  const pool = [
    ['宁德时代', '300750', '电池',       210],
    ['中际旭创', '300308', 'CPO',          98],
    ['比亚迪',   '002594', '新能源车',  235],
    ['立讯精密', '002475', '消费电子',   38],
    ['迈瑞医疗', '300760', '医疗器械', 268],
    ['北方华创', '002371', '半导体设备', 312],
    ['汇川技术', '300124', '工控',         62],
    ['亿纬锂能', '300014', '电池',         44],
    ['新易盛',   '300502', 'CPO',         118],
    ['天孚通信', '300394', '光模块',      154],
  ];
  return pool.slice(0, 8).map((p, i) => ({
    name: p[0], code: p[1], industry: p[2], anchor: p[3],
    score: +((2.1 - i*0.18) + Math.sin((i+seed)*1.3)*0.12).toFixed(3),
    weight: +(((20 - i*1.8))).toFixed(1),
    chg: +((Math.sin((i+seed)*0.9)*3.6) + (i % 2 ? 1.2 : -0.4)).toFixed(2),
  }));
}

// 给某只票生成 180 个交易日的价格序列 + 因子驱动的买卖点
function genStockTape(pick, factorSign = -1) {
  // factorSign: alpha#41 是负向因子, 买入 = 因子负向 = 价格回落到 vwap 之下
  const N = 180;
  const anchor = pick.anchor || 100;
  const drift = factorSign < 0 ? 0.0008 : 0.0006;
  const points = [];
  let p = anchor * 0.85;
  for (let i = 0; i < N; i++) {
    const cycle = Math.sin(i * 0.10) * 0.022 + Math.sin(i * 0.31) * 0.012;
    const noise = Math.sin(i * 1.7 + anchor) * 0.008;
    p = p * (1 + drift + cycle * 0.18 + noise);
    const high = p * (1 + Math.abs(Math.sin(i*0.9)) * 0.007 + 0.003);
    const low  = p * (1 - Math.abs(Math.cos(i*0.7)) * 0.007 - 0.003);
    const vol = 0.6 + Math.abs(Math.sin(i*0.4 + anchor*0.01)) + Math.abs(Math.cos(i*1.1))*0.3;
    points.push({ close: +p.toFixed(2), high: +high.toFixed(2), low: +low.toFixed(2), vol: +vol.toFixed(2) });
  }
  // 信号: 每隔 ~25 天一组进出 (alpha#41 月频换手), 180 天目标 4-6 对
  const signals = [];
  let cur = null;
  let cooldown = 0;
  for (let i = 6; i < N - 2; i++) {
    cooldown--;
    const c = points[i].close;
    const ma10 = points.slice(Math.max(0, i-10), i).reduce((s, x) => s + x.close, 0) / Math.min(10, i);
    const dev = (c - ma10) / ma10;
    // 入场: 短期回到 10 日均线之下 (因子值进入 top decile)
    if (!cur && cooldown <= 0 && dev < -0.005) {
      cur = { in: i, inPrice: c };
    } else if (cur) {
      const held = i - cur.in;
      // 出场: 持仓 >= 10 天后, dev > 1.2% 或 持仓满 22 天
      if (held >= 10 && (dev > 0.012 || held >= 22)) {
        signals.push({ in: cur.in, inPrice: cur.inPrice, out: i, outPrice: c });
        cur = null;
        cooldown = 3;
      }
    }
  }
  if (cur) signals.push({ in: cur.in, inPrice: cur.inPrice, out: null, outPrice: null });
  return { points, signals };
}

// ───────── 顶部 ─────────
function TopBar({ mode, onMode }) {
  return (
    <header style={{ padding: '12px 28px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 18, flexShrink: 0, background: 'rgba(241,234,217,0.5)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="seal" style={{ width: 28, height: 28, fontSize: 15 }}>觀</span>
        <div>
          <div className="serif" style={{ fontSize: 14, fontWeight: 500, color: 'var(--ink)', letterSpacing: '0.06em' }}>觀瀾 · 量化研究</div>
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em', marginTop: 1 }}>QUANT WORKBENCH · ALPHA RESEARCH</div>
        </div>
      </div>

      <nav style={{ display: 'flex', alignItems: 'center', gap: 0, marginLeft: 28 }}>
        {[
          { k: 'chat',  l: '对话研究' },
          { k: 'quant', l: '量化专栏' },
          { k: 'watch', l: '盯盘' },
          { k: 'memo',  l: '研究档案' },
        ].map(t => (
          <button key={t.k} onClick={() => onMode(t.k)} className="hover-pill" style={{
            padding: '6px 12px', border: 'none', background: 'transparent',
            fontFamily: 'var(--serif)', fontSize: 12.5,
            color: mode === t.k ? 'var(--ink)' : 'var(--ink-2)',
            borderBottom: mode === t.k ? '2px solid var(--yin)' : '2px solid transparent',
            cursor: 'pointer',
          }}>{t.l}</button>
        ))}
      </nav>

      <div style={{ flex: 1 }} />

      <div className="mono" style={{ display: 'flex', alignItems: 'center', gap: 14, fontSize: 11 }}>
        {[{n:'上证',v:'3,287',d:'+0.46'},{n:'沪深300',v:'3,941',d:'+0.62'},{n:'中证500',v:'5,624',d:'+0.81'}].map((x,i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
            <span style={{ color: 'var(--ink-3)', fontSize: 10 }}>{x.n}</span>
            <span style={{ color: 'var(--ink-1)' }}>{x.v}</span>
            <span className={x.d.startsWith('-') ? 'down' : 'up'}>{x.d}%</span>
          </div>
        ))}
      </div>

      <div style={{ width: 1, height: 20, background: 'var(--line)' }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }} className="mono">
        <span style={{ width: 5, height: 5, background: 'var(--zhu)', borderRadius: '50%' }} />
        <span style={{ fontSize: 10, color: 'var(--ink-2)' }}>交易中 · 14:17</span>
      </div>
    </header>
  );
}

// ───────── 左：对话栏 ─────────
const SEED_CHAT = [
  { role: 'user', text: '帮我看下近一年价量类因子里, IC IR 最高的那些' },
  { role: 'ai', kind: 'plan', label: '价量因子 · IC IR 排序', tools: [
      { name: 'alpha_list',     cn: '因子库',   args: 'cat=价量,window=1y', t: 0.2 },
      { name: 'alpha_bench',    cn: '跑因子',   args: 'top=12,by=ICIR',     t: 18.4 },
      { name: 'alpha_snapshot', cn: '因子快照', args: 'pool=zz500',         t: 1.1 },
  ]},
  { role: 'ai', kind: 'answer', text:
    `近 1 年沪深 300 + 中证 500 池里, 价量类 IC IR 排前几位的:\n` +
    `①  alpha#41  ICIR -1.82  (高低振幅负向)\n` +
    `②  qlib#42   ICIR  1.84  (K 线中位 KMID)\n` +
    `③  alpha#94  ICIR  1.71  (vwap 极值排序)\n` +
    `右侧已经把 alpha#41 加载到工作台, 含 24 个月 IC 序列、十分位曲线和长短组合净值. 要不要把它和 qlib#42 合成?`,
  },
  { role: 'user', text: '顺便, 把我上周盘后笔记里那个 “连续 3 天放量上涨 + MACD 金叉” 变成一个因子' },
  { role: 'ai', kind: 'plan', label: '炼因子 · 经验 → 公式', tools: [
      { name: 'memory_recall', cn: '记忆召回', args: 'topic="放量+MACD金叉", since=90d', t: 0.4 },
      { name: 'alpha_forge',   cn: '炼因子',  args: 'natural=...,pool=zz500',        t: 8.2 },
      { name: 'alpha_bench',   cn: '速测',     args: 'window=2y,freq=daily,hold=5d',  t: 6.4 },
  ]},
  { role: 'ai', kind: 'alchemy', data: {
      quote: '我发现连续 3 天放量上涨之后, MACD 一旦金叉, 下一周走势经常比较好, 尤其是中证 500 池里的中盘股. 之前已经看到了好几次.',
      source: '9 月 14 日盘后笔记 · 11 月 6 日追补',
      observations: 14,
      parsed: [
        { k: '触发', v: '连续 3 日  close↑  +  vol_ratio > 1.2' },
        { k: '叠加', v: 'MACD(12,26,9)  当日金叉  cross↑' },
        { k: '方向', v: '多头 · 按 vol_ratio 倒序打分' },
        { k: '持有', v: '5 个交易日  /  遇 -3% 止损' },
        { k: '池',   v: 'ZZ500 · 剩下 412 只 (过滤 ST、停牌)' },
      ],
      formula:
`SIGNAL = WHERE(
    ALL(CLOSE > DELAY(CLOSE, 1), 3)
    AND  VOL_RATIO(3) > 1.2
    AND  CROSS(MACD(12,26,9), DEA(9)),
  RANK(VOL_RATIO(3)),    · 多头打分
  NaN
)`,
      kpis: [
        { l: '触发次数', v: '412',     dir: null },
        { l: '胜率',     v: '58.0%',   dir: 'up' },
        { l: '均收益',   v: '+1.62%', dir: 'up' },
        { l: 'ICIR',     v: '1.34',    dir: null },
      ],
      cumReturn: [1.0,1.012,1.024,1.018,1.031,1.046,1.040,1.058,1.072,1.066,1.084,1.098,1.092,1.108,1.122,1.116,1.134,1.150,1.142,1.158,1.176,1.182,1.198,1.214,1.226],
  }},
  { role: 'ai', kind: 'answer', text:
    `炼出来了, 叫你看看. 近 2 年在 ZZ500 上触发 412 次, 胜率 58%, 平均单次 +1.62%, ICIR 也过了 1. 你的原话里“中盘股”我暂时用 ZZ500 代理, 要不要换成明确的市值区间 (50–300 亿)? 另外 MACD 金叉这个条件可以加个肣体高度阈值, 可能胜率还能再抬一点.`
  },
];

function ChatPanel({ onSelectFactor, selected, onAddFactor, userFactors }) {
  const [draft, setDraft] = useState('');
  const [chat, setChat] = useState(SEED_CHAT);
  const scroller = useRef(null);

  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [chat]);

  const send = () => {
    if (!draft.trim()) return;
    setChat(c => [...c, { role: 'user', text: draft.trim() }]);
    const ask = draft.trim();
    setDraft('');
    setTimeout(() => {
      setChat(c => [...c, { role: 'ai', kind: 'plan', label: '调度中…', tools: [
        { name: 'alpha_bench', cn: '跑因子', args: '...', t: 0, running: true },
      ]}]);
    }, 250);
    setTimeout(() => {
      setChat(c => c.map((m, i) => i === c.length - 1
        ? { ...m, label: '回测完成', tools: m.tools.map(t => ({ ...t, running: false, t: 14.6 })) }
        : m));
      setChat(c => [...c, { role: 'ai', kind: 'answer', text:
        `已经在中证 500 池上, 月频, 跑了 alpha#41 的长短组合.  右侧工作台已更新:\n` +
        `· 12 期 IC 均值 -4.6% · ICIR -1.82\n` +
        `· 多空年化 18.6% · 最大回撤 -7.2%\n` +
        `· 第 10 分位组超额最高, 信号符合预期 (负相关因子做反向).` }]);
    }, 1400);
  };

  return (
    <aside style={{ width: 460, flexShrink: 0, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', background: 'rgba(241,234,217,0.55)' }}>
      <div style={{ padding: '12px 20px 10px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <span className="serif" style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>对话研究</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>· QUANT THREAD</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>当前因子 ·</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--yin)', fontWeight: 500 }}>{selected}</span>
      </div>

      <div ref={scroller} style={{ flex: 1, overflowY: 'auto', padding: '18px 22px 12px', display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0 }}>
        {chat.map((m, i) => {
          if (m.role === 'user') return (
            <div key={i} style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <div style={{ maxWidth: '82%', padding: '10px 14px', background: 'var(--ink)', color: 'var(--paper)',
                fontFamily: 'var(--serif)', fontSize: 13.5, lineHeight: 1.65, animation: 'fadeIn 200ms ease-out' }}>{m.text}</div>
            </div>
          );
          if (m.kind === 'plan') return <ToolChain key={i} msg={m} />;
          if (m.kind === 'alchemy') return <AlchemyCard key={i} data={m.data} onAdd={onAddFactor} added={(userFactors || []).some(f => f.id === 'usr_α027')} />;
          if (m.kind === 'answer') return (
            <div key={i} style={{ display: 'flex', gap: 10, animation: 'fadeIn 200ms ease-out' }}>
              <Avatar />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="serif" style={{ fontSize: 13.5, color: 'var(--ink-1)', lineHeight: 1.78, whiteSpace: 'pre-wrap' }}>{m.text}</div>
              </div>
            </div>
          );
          return null;
        })}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
          {[
            '把我上周记的“高股息 + 低负债”炼成因子',
            '把 alpha#41 和 qlib#42 合成等权',
            '换到沪深 300 再跑一次',
            '看下因子的拥挤度',
          ].map((s, i) => (
            <button key={i} onClick={() => setDraft(s)} className="hover-pill" style={{
              padding: '4px 10px', border: '1px solid var(--line)', background: 'var(--paper)',
              fontFamily: 'var(--serif)', fontSize: 11.5, color: 'var(--ink-2)', cursor: 'pointer'
            }}>❯ {s}</button>
          ))}
        </div>
      </div>

      <div style={{ padding: '10px 18px 14px', borderTop: '1px solid var(--line)', background: 'rgba(255,255,255,0.35)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8, border: '1px solid var(--ink)', padding: '8px 12px', background: 'var(--paper)' }}>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder="问一个因子, 一个组合, 一个回测想法…"
            rows={2}
            style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', resize: 'none',
              fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--ink)', lineHeight: 1.55 }}
          />
          <button onClick={send} style={{ background: 'var(--ink)', color: 'var(--paper)', border: 'none', padding: '6px 14px',
            fontFamily: 'var(--serif)', fontSize: 12, letterSpacing: '0.05em', cursor: 'pointer' }}>送出</button>
        </div>
        <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', display: 'flex', gap: 14, marginTop: 8, letterSpacing: '0.05em' }}>
          <span>/alpha 跑因子</span>
          <span>/combine 合成</span>
          <span>/screen 选股</span>
          <span style={{ flex: 1 }} />
          <span>⌘ Enter 送出</span>
        </div>
      </div>
    </aside>
  );
}

function Avatar() {
  return <div style={{ width: 26, height: 26, flex: '0 0 26px', background: 'var(--paper-2)', border: '1px solid var(--ink)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink)' }}>觀</div>;
}

function buildAlchemyFormula(p) {
  return `SIGNAL = WHERE(
    ALL(CLOSE > DELAY(CLOSE, 1), ${p.consec})
    AND  VOL_RATIO(${p.consec}) > ${p.volRatio.toFixed(1)}
    AND  CROSS(MACD(12,26,9), DEA(9)),
  RANK(VOL_RATIO(${p.consec})),    · 多头打分
  NaN
)
HOLD = ${p.hold} d  ·  STOP = ${p.stopLoss.toFixed(1)}%`;
}

function alchemyKpis(p) {
  const volMul = 1.2 / Math.max(p.volRatio, 0.1);
  const consecMul = 3 / Math.max(p.consec, 1);
  const triggers = Math.max(40, Math.round(412 * volMul * consecMul * (p.hold / 5) * 0.55 + 90));
  const winRate = Math.max(45, Math.min(72, +(58 + (p.volRatio - 1.2) * 4.2 + (p.consec - 3) * 1.4 + (p.stopLoss + 3) * 0.5).toFixed(1)));
  const avgRet = +(1.62 + (p.volRatio - 1.2) * 0.6 + (p.hold - 5) * 0.18 + (p.stopLoss + 3) * 0.14).toFixed(2);
  const icir = +(1.34 + (p.volRatio - 1.2) * 0.32 + (p.consec - 3) * 0.08).toFixed(2);
  return [
    { l: '触发', v: String(triggers), dir: null },
    { l: '胜率', v: winRate.toFixed(1) + '%', dir: 'up' },
    { l: '均收益', v: (avgRet >= 0 ? '+' : '') + avgRet.toFixed(2) + '%', dir: avgRet >= 0 ? 'up' : 'down' },
    { l: 'ICIR', v: icir.toFixed(2), dir: null },
  ];
}

function buildUserFactorEntry(p, kpis) {
  const avg = parseFloat(kpis[2].v);
  const icirN = parseFloat(kpis[3].v);
  return {
    id: 'usr_α027',
    lib: 'alpha101',
    cat: '用户',
    icm: +(icirN * 0.026).toFixed(4),
    icir: icirN,
    sharpe: +(icirN * 0.92).toFixed(2),
    ar: +(avg * 11).toFixed(1),
    mdd: -6.4,
    turn: Math.round(100 / Math.max(p.hold, 1) * 4.5),
    hot: true,
    user: true,
    formula: `连续放量 ${p.consec} 日 + MACD 金叉 · 持有 ${p.hold} 日 · 量比 > ${p.volRatio.toFixed(1)}`,
  };
}

function AlchemyCard({ data, onAdd, added }) {
  const [expanded, setExpanded] = useState(true);
  const [editing, setEditing] = useState(false);
  const [p, setP] = useState({ consec: 3, volRatio: 1.2, hold: 5, stopLoss: -3 });
  const formula = useMemo(() => buildAlchemyFormula(p), [p]);
  const kpis = useMemo(() => alchemyKpis(p), [p]);
  const dirty = p.consec !== 3 || Math.abs(p.volRatio - 1.2) > 0.001 || p.hold !== 5 || Math.abs(p.stopLoss - (-3)) > 0.001;
  const reset = () => setP({ consec: 3, volRatio: 1.2, hold: 5, stopLoss: -3 });

  const handleAdd = () => {
    if (added || !onAdd) return;
    onAdd(buildUserFactorEntry(p, kpis));
  };

  // ───── 折叠态 ─────
  if (!expanded) {
    return (
      <div style={{ display: 'flex', gap: 10, animation: 'fadeIn 240ms ease-out' }}>
        <Avatar />
        <div onClick={() => setExpanded(true)} style={{
          flex: 1, minWidth: 0, background: 'var(--paper)',
          border: '1.5px solid var(--ink)',
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '7px 12px 7px 10px', cursor: 'pointer',
        }}>
          <div style={{
            width: 22, height: 22, background: 'var(--yin)', color: 'var(--paper)',
            fontFamily: 'var(--serif)', fontSize: 12, fontWeight: 500,
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}>炼</div>
          <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>经验 → 因子</span>
          <code className="mono" style={{ fontSize: 10.5, color: 'var(--ink-1)' }}>usr_α027</code>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            · {kpis[0].v} 触发 · {kpis[1].v} 胜率 · {kpis[2].v} 均收益 · ICIR {kpis[3].v}
          </span>
          {added && <span className="mono" style={{ fontSize: 9.5, padding: '1px 6px', background: 'var(--dai)', color: 'var(--paper)', flexShrink: 0 }}>✓ 已入库</span>}
          {dirty && !added && <span className="mono" style={{ fontSize: 9.5, color: 'var(--jin)', flexShrink: 0 }}>已改 ●</span>}
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', flexShrink: 0 }}>展开 ▾</span>
        </div>
      </div>
    );
  }

  // ───── 展开态 ─────
  return (
    <div style={{ display: 'flex', gap: 10, animation: 'fadeIn 280ms ease-out' }}>
      <Avatar />
      <div style={{
        flex: 1, minWidth: 0, background: 'var(--paper)',
        border: '1.5px solid var(--ink)', position: 'relative',
        boxShadow: '6px 6px 0 -2px var(--paper-3)',
      }}>
        {/* 角章 */}
        <div style={{
          position: 'absolute', top: -1, right: -1, width: 30, height: 30,
          background: 'var(--yin)', color: 'var(--paper)',
          fontFamily: 'var(--serif)', fontSize: 14, fontWeight: 500,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>炼</div>

        {/* 标题 */}
        <div style={{ padding: '10px 14px 8px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'baseline', gap: 8, paddingRight: 38 }}>
          <span className="serif" style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>经验 → 因子</span>
          <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em' }}>α-FORGE</span>
          <span style={{ flex: 1 }} />
          <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>id · usr_α027</span>
          {added && <span className="mono" style={{ fontSize: 9, padding: '1px 5px', background: 'var(--dai)', color: 'var(--paper)' }}>✓ 已入库</span>}
          <span className="mono hover-pill" onClick={() => setExpanded(false)} style={{
            fontSize: 9, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 5px', border: '1px solid var(--line)'
          }}>收起 ▴</span>
        </div>

        {/* 原话 */}
        <div style={{ padding: '12px 14px 10px' }}>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6 }}>原话 · 经验记忆</div>
          <div className="serif" style={{
            fontSize: 13, color: 'var(--ink-1)', lineHeight: 1.72, fontStyle: 'italic',
            paddingLeft: 12, borderLeft: '2px solid var(--jin)', position: 'relative',
          }}>
            <span style={{ position: 'absolute', top: -8, left: -3, color: 'var(--jin)', fontSize: 26, fontFamily: 'var(--serif)', lineHeight: 1 }}>「</span>
            <span>{data.quote}</span>
          </div>
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 6, display: 'flex', gap: 12 }}>
            <span>来源 · {data.source}</span>
            <span style={{ flex: 1 }} />
            <span>{data.observations} 次类似观察</span>
          </div>
        </div>

        {/* 解析 · 信号要素 (or 调参) */}
        <div style={{ borderTop: '1px dashed var(--line)', padding: '10px 14px' }}>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
            <span>{editing ? '调参 · 编辑信号要素' : '解析 · 信号要素'}</span>
            <span style={{ width: 18, height: 1, background: 'var(--line)' }} />
            <span>memory_recall ✓</span>
            <span style={{ flex: 1 }} />
            {editing && dirty && (
              <span onClick={reset} className="hover-pill" style={{
                cursor: 'pointer', padding: '0 5px', color: 'var(--ink-2)', border: '1px solid var(--line)'
              }}>还原 ↺</span>
            )}
          </div>
          {editing ? (
            <AlchemyParamEditor p={p} setP={setP} />
          ) : (
            [
              { k: '触发', v: `连续 ${p.consec} 日  close↑  +  vol_ratio > ${p.volRatio.toFixed(1)}` },
              { k: '叠加', v: 'MACD(12,26,9)  当日金叉  cross↑' },
              { k: '方向', v: '多头 · 按 vol_ratio 倒序打分' },
              { k: '持有', v: `${p.hold} 个交易日  /  遇 ${p.stopLoss.toFixed(1)}% 止损` },
              { k: '池',   v: 'ZZ500 · 剩下 412 只 (过滤 ST、停牌)' },
            ].map((row, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, padding: '3px 0' }}>
                <span className="serif" style={{ width: 36, fontSize: 11.5, color: 'var(--ink-3)', flexShrink: 0 }}>{row.k}</span>
                <code style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-1)', lineHeight: 1.55 }}>{row.v}</code>
              </div>
            ))
          )}
        </div>

        {/* 公式 */}
        <div style={{ borderTop: '1px dashed var(--line)', padding: '10px 14px', background: 'rgba(28,24,20,0.04)' }}>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
            <span>因子公式</span>
            <span style={{ width: 18, height: 1, background: 'var(--line)' }} />
            <span>alpha_forge ✓</span>
            {dirty && <span style={{ color: 'var(--jin)' }}>· 已重生成</span>}
            <span style={{ flex: 1 }} />
            <span onClick={() => setEditing(!editing)} className="hover-pill" style={{
              cursor: 'pointer', padding: '0 6px',
              color: editing ? 'var(--paper)' : 'var(--ink-2)',
              border: '1px solid ' + (editing ? 'var(--yin)' : 'var(--line)'),
              background: editing ? 'var(--yin)' : 'transparent',
            }}>{editing ? '完成 ✓' : '改一改 ✎'}</span>
          </div>
          <pre style={{
            margin: 0, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink)',
            lineHeight: 1.7, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          }}>{formula}</pre>
        </div>

        {/* 速测 */}
        <div style={{ borderTop: '1px dashed var(--line)', padding: '10px 14px' }}>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
            <span>速测 · ZZ500 · 23-06 → 25-05</span>
            <span style={{ width: 18, height: 1, background: 'var(--line)' }} />
            <span>alpha_bench ✓</span>
            <span style={{ flex: 1 }} />
            {dirty && <span style={{ color: 'var(--jin)' }}>● 近似估算 · 完整重跑 ↻</span>}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0, border: '1px solid var(--line-soft)' }}>
            {kpis.map((k, i) => (
              <div key={i} style={{ padding: '7px 9px', borderRight: i < kpis.length - 1 ? '1px solid var(--line-soft)' : 'none' }}>
                <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>{k.l}</div>
                <div className={'mono ' + (k.dir === 'up' ? 'up' : k.dir === 'down' ? 'down' : '')}
                  style={{ fontSize: 13, color: k.dir ? undefined : 'var(--ink)', fontWeight: 500, marginTop: 2 }}>{k.v}</div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 8 }}>
            <AlchemySpark data={data.cumReturn} />
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', display: 'flex', justifyContent: 'space-between', marginTop: 3 }}>
              <span>23-06</span>
              <span>累计收益 · 多头组合</span>
              <span>25-05</span>
            </div>
          </div>
        </div>

        {/* 动作 */}
        <div style={{ borderTop: '1px solid var(--line)', padding: '8px 12px', display: 'flex', gap: 6, alignItems: 'center' }}>
          <button onClick={handleAdd} disabled={added} style={{
            flex: 1, padding: '7px 10px',
            background: added ? 'transparent' : 'var(--ink)',
            color: added ? 'var(--dai)' : 'var(--paper)',
            border: added ? '1px solid var(--dai)' : 'none',
            fontFamily: 'var(--serif)', fontSize: 12, letterSpacing: '0.04em',
            cursor: added ? 'default' : 'pointer',
          }}>{added ? '✓ 已加入因子库 · 工作台已切换' : '加入因子库 ↗'}</button>
          <button style={{
            padding: '7px 10px', background: 'transparent', color: 'var(--ink)',
            border: '1px solid var(--ink)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer',
          }}>工作台跑深度</button>
          <button onClick={() => setEditing(!editing)} style={{
            padding: '7px 10px',
            background: editing ? 'var(--yin)' : 'transparent',
            color: editing ? 'var(--paper)' : 'var(--ink-2)',
            border: '1px solid ' + (editing ? 'var(--yin)' : 'var(--line)'),
            fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer',
          }}>{editing ? '收起编辑' : '改一改'}</button>
        </div>
      </div>
    </div>
  );
}

function AlchemyParamEditor({ p, setP }) {
  const rows = [
    { k: 'consec',   l: '连续上涨', unit: '日',   min: 1,   max: 5,   step: 1,   fmt: v => String(v) },
    { k: 'volRatio', l: '量比阈值', unit: '×',    min: 1.0, max: 2.0, step: 0.1, fmt: v => v.toFixed(1) },
    { k: 'hold',     l: '持有天数', unit: '日',   min: 1,   max: 20,  step: 1,   fmt: v => String(v) },
    { k: 'stopLoss', l: '止损',    unit: '%',    min: -10, max: 0,   step: 0.5, fmt: v => v.toFixed(1) },
  ];
  return (
    <div>
      {rows.map(r => (
        <div key={r.k} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '5px 0' }}>
          <span className="serif" style={{ width: 60, fontSize: 11.5, color: 'var(--ink-2)', flexShrink: 0 }}>{r.l}</span>
          <input type="range" min={r.min} max={r.max} step={r.step} value={p[r.k]}
            onChange={(e) => setP({ ...p, [r.k]: parseFloat(e.target.value) })}
            style={{ flex: 1, accentColor: 'var(--yin)', height: 4 }} />
          <code style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)', width: 44, textAlign: 'right', fontWeight: 500 }}>{r.fmt(p[r.k])}</code>
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', width: 18 }}>{r.unit}</span>
        </div>
      ))}
    </div>
  );
}

function AlchemySpark({ data }) {
  const w = 380, h = 40;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const x = (i) => (i / (data.length - 1)) * w;
  const y = (v) => h - ((v - min) / (max - min || 1)) * (h - 6) - 3;
  const path = data.map((v, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  const fillPath = path + ` L ${w} ${h} L 0 ${h} Z`;
  const endY = y(data[data.length - 1]);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: '100%', height: 42, display: 'block' }}>
      <line x1="0" x2={w} y1={y(1)} y2={y(1)} stroke="var(--ink-3)" strokeWidth="0.5" strokeDasharray="2 3" />
      <path d={fillPath} fill="var(--zhu)" opacity="0.12" />
      <path d={path} stroke="var(--ink)" strokeWidth="1.3" fill="none" />
      <circle cx={w - 1} cy={endY} r="2.5" fill="var(--yin)" />
      <text x={w - 6} y={endY - 5} fontSize="9" fontFamily="var(--mono)" fill="var(--yin)" textAnchor="end" fontWeight="500">
        +{((data[data.length - 1] - 1) * 100).toFixed(1)}%
      </text>
    </svg>
  );
}

function ToolChain({ msg }) {
  return (
    <div style={{ display: 'flex', gap: 10, animation: 'fadeIn 200ms ease-out' }}>
      <Avatar />
      <div style={{ flex: 1, minWidth: 0, border: '1px solid var(--line-soft)', background: 'rgba(255,255,255,0.55)' }}>
        <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="serif" style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink)' }}>研究链 · {msg.label}</span>
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginLeft: 'auto' }}>
            {msg.tools.length} 个工具
          </span>
        </div>
        <div style={{ padding: '4px 0' }}>
          {msg.tools.map((tl, i) => (
            <div key={i} style={{ display: 'flex', gap: 10, padding: '5px 12px', alignItems: 'center' }}>
              <span style={{
                width: 18, height: 18, flex: '0 0 18px',
                background: tl.running ? 'var(--yin)' : 'var(--ink)',
                color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 10,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>{i+1}</span>
              <code style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink)', fontWeight: 500 }}>{tl.name}</code>
              <span className="serif" style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{tl.cn}</span>
              <code style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-3)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tl.args}</code>
              <span className="mono" style={{ fontSize: 10, color: tl.running ? 'var(--yin)' : 'var(--ink-3)', whiteSpace: 'nowrap' }}>
                {tl.running ? '⠋ 运行中…' : `✓ ${tl.t}s`}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ───────── 右：量化工作台 ─────────
function QuantWorkbench({ selected, onSelect, lib, onLib, pool, onPool, freq, onFreq, focus, onFocus, userFactors = [] }) {
  const allKnown = [...userFactors, ...FACTORS];
  const factor = allKnown.find(f => f.id === selected) || FACTORS[2];
  const ic = useMemo(() => genICSeries(factor.icm * 100), [factor.id]);
  const equity = useMemo(() => genEquity(factor.icm * 100), [factor.id]);
  const decile = useMemo(() => genDecile(factor.icm * 100), [factor.id]);
  const picks = useMemo(() => genPicks(factor.icm * 100), [factor.id]);

  const listed = [...userFactors, ...FACTORS.filter(f => f.lib === lib)];

  return (
    <section style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* 控制条 */}
      <div style={{ padding: '10px 26px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 22, background: 'rgba(241,234,217,0.4)', flexShrink: 0, whiteSpace: 'nowrap' }}>
        <Pill label="因子库">
          <Segmented value={lib} onChange={onLib}
            options={[
              { value: 'alpha101', label: 'Alpha101' },
              { value: 'gtja191',  label: 'GTJA191' },
              { value: 'qlib158',  label: 'Qlib158' },
            ]} />
        </Pill>
        <Pill label="股票池">
          <Segmented value={pool} onChange={onPool}
            options={[
              { value: 'hs300',  label: 'HS300' },
              { value: 'zz500',  label: 'ZZ500' },
              { value: 'zz1000', label: 'ZZ1000' },
              { value: 'all',    label: '全市场' },
            ]} />
        </Pill>
        <Pill label="频率">
          <Segmented value={freq} onChange={onFreq}
            options={[
              { value: 'day',   label: '日' },
              { value: 'week',  label: '周' },
              { value: 'month', label: '月' },
            ]} />
        </Pill>
        <Pill label="区间">
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-1)' }}>23-06 → 25-05</span>
        </Pill>
        <div style={{ flex: 1 }} />
        <button className="hover-pill" style={{
          background: 'transparent', border: '1px solid var(--line)', padding: '5px 12px',
          fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-2)', cursor: 'pointer',
          whiteSpace: 'nowrap',
        }}>↻ 重跑回测</button>
        <button style={{
          background: 'var(--ink)', color: 'var(--paper)', border: 'none', padding: '6px 14px',
          fontFamily: 'var(--serif)', fontSize: 12, letterSpacing: '0.04em', cursor: 'pointer',
          whiteSpace: 'nowrap',
        }}>＋ 加入组合</button>
      </div>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
        {/* 横条因子带 — 代替之前的侧列 */}
        <FactorStrip factors={listed} selected={selected} onSelect={onSelect} libMeta={LIBS.find(l => l.id === lib)} />

        {/* 因子详情 */}
        <FactorDetail factor={factor} ic={ic} equity={equity} decile={decile} picks={picks} pool={pool} freq={freq} focus={focus} onFocus={onFocus} />
      </div>
    </section>
  );
}

function FactorStrip({ factors, selected, onSelect, libMeta }) {
  const [hover, setHover] = useState(null);  // {id, anchor: HTMLElement}
  return (
    <div style={{
      padding: '9px 26px', borderBottom: '1px solid var(--line-soft)',
      display: 'flex', alignItems: 'center', gap: 8, overflowX: 'auto', overflowY: 'visible',
      background: 'rgba(241,234,217,0.28)', flexShrink: 0, whiteSpace: 'nowrap', position: 'relative',
    }}>
      <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.18em', flexShrink: 0, marginRight: 4 }}>本库因子</span>
      {factors.map(f => {
        const active = selected === f.id;
        const isUser = f.user === true;
        return (
          <button key={f.id}
            onClick={() => onSelect(f.id)}
            onMouseEnter={(e) => setHover({ id: f.id, rect: e.currentTarget.getBoundingClientRect(), wrapper: e.currentTarget.parentElement.getBoundingClientRect() })}
            onMouseLeave={() => setHover(null)}
            style={{
            flexShrink: 0, cursor: 'pointer',
            padding: '4px 10px',
            display: 'inline-flex', alignItems: 'center', gap: 7,
            background: active ? 'var(--paper)' : (isUser ? 'rgba(168,57,45,0.05)' : 'transparent'),
            border: active
              ? '1px solid var(--yin)'
              : (isUser ? '1px dashed var(--yin)' : '1px solid var(--line)'),
            borderLeft: active ? '2px solid var(--yin)' : (isUser ? '1px dashed var(--yin)' : '1px solid var(--line)'),
            fontFamily: 'var(--mono)', fontSize: 11,
            color: active ? 'var(--ink)' : 'var(--ink-2)',
            position: 'relative',
          }}>
            {isUser && <span className="serif" style={{ fontSize: 9, padding: '0 4px', background: 'var(--yin)', color: 'var(--paper)', letterSpacing: 0 }}>新</span>}
            <span style={{ fontWeight: active ? 500 : 400 }}>{f.id}</span>
            <span className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{f.cat}</span>
            <span style={{ fontSize: 10, color: Math.abs(f.icir) > 1.6 ? 'var(--yin)' : 'var(--ink-3)' }}>{f.icir >= 0 ? '+' : ''}{f.icir.toFixed(2)}</span>
            {f.hot && !isUser && <span style={{ width: 5, height: 5, background: 'var(--yin)', flexShrink: 0 }} />}
          </button>
        );
      })}
      <button style={{
        flexShrink: 0, padding: '4px 10px', background: 'transparent',
        border: '1px dashed var(--line)', cursor: 'pointer',
        fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-2)',
      }}>+ 载入全部 ({factors.length} / {libMeta?.count})</button>
      <span style={{ flex: 1 }} />
      <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', display: 'flex', gap: 10, flexShrink: 0 }}>
        <span>按 ICIR 降序 ▾</span>
        <span className="hover-pill" style={{ cursor: 'pointer', padding: '0 4px' }}>⊕ 新因子</span>
      </div>

      {hover && (() => {
        const f = factors.find(x => x.id === hover.id);
        if (!f) return null;
        const left = hover.rect.left - hover.wrapper.left;
        const top = hover.rect.bottom - hover.wrapper.top + 6;
        const sp = Array.from({ length: 24 }, (_, i) => 1 + Math.sin(i * 0.5 + f.icir * 5) * 0.08 * Math.sign(f.icir) + i * 0.005 * Math.sign(f.icir));
        return (
          <div style={{
            position: 'absolute', left: Math.max(8, left), top, zIndex: 20,
            width: 240, background: 'var(--paper)', border: '1px solid var(--ink)',
            boxShadow: '4px 4px 0 -1px var(--paper-3)', pointerEvents: 'none',
          }}>
            <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <code style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)', fontWeight: 500 }}>{f.id}</code>
              <span className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{f.cat}</span>
              <span style={{ flex: 1 }} />
              {f.hot && <span className="mono" style={{ fontSize: 9, color: 'var(--yin)' }}>● 热门</span>}
              {f.user && <span className="serif" style={{ fontSize: 9, padding: '0 4px', background: 'var(--yin)', color: 'var(--paper)' }}>新</span>}
            </div>
            <div style={{ padding: '6px 10px 8px' }}>
              <MiniSparkline data={sp} w={218} h={32} color="var(--ink)" filled />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0, marginTop: 6, border: '1px solid var(--line-soft)' }}>
                {[
                  { l: 'ICIR', v: f.icir.toFixed(2), c: Math.abs(f.icir) > 1.6 ? 'var(--yin)' : null },
                  { l: 'Sharpe', v: f.sharpe.toFixed(2) },
                  { l: '年化', v: f.ar.toFixed(1) + '%' },
                  { l: '回撤', v: f.mdd.toFixed(1) + '%', c: 'var(--dai)' },
                ].map((k, i, arr) => (
                  <div key={i} style={{ padding: '4px 5px', borderRight: i < arr.length - 1 ? '1px solid var(--line-soft)' : 'none' }}>
                    <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '0.14em' }}>{k.l}</div>
                    <div className="mono" style={{ fontSize: 11, color: k.c || 'var(--ink)', fontWeight: 500, marginTop: 1 }}>{k.v}</div>
                  </div>
                ))}
              </div>
              <code style={{ display: 'block', fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--ink-2)', marginTop: 6, lineHeight: 1.4, maxHeight: 26, overflow: 'hidden', wordBreak: 'break-all' }}>{f.formula}</code>
            </div>
          </div>
        );
      })()}
    </div>
  );
}

function Pill({ label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>{label}</span>
      {children}
    </div>
  );
}

function Segmented({ value, onChange, options }) {
  return (
    <div style={{ display: 'inline-flex', border: '1px solid var(--line)' }}>
      {options.map((o, i) => (
        <button key={o.value} onClick={() => onChange(o.value)} style={{
          padding: '5px 11px', border: 'none',
          background: value === o.value ? 'var(--ink)' : 'transparent',
          color: value === o.value ? 'var(--paper)' : 'var(--ink-2)',
          fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer',
          whiteSpace: 'nowrap',
          borderRight: i < options.length - 1 ? '1px solid var(--line)' : 'none',
        }}>{o.label}</button>
      ))}
    </div>
  );
}

function FactorList({ factors, selected, onSelect, lib }) {
  const meta = LIBS.find(l => l.id === lib);
  return (
    <div style={{ width: 268, flexShrink: 0, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', background: 'rgba(241,234,217,0.25)' }}>
      <div style={{ padding: '12px 16px 8px', borderBottom: '1px solid var(--line-soft)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span className="serif" style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>{meta.name}</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>· {meta.count}</span>
          <span style={{ flex: 1 }} />
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>已展示 {factors.length}</span>
        </div>
        <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 3, letterSpacing: '0.05em' }}>{meta.src}</div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10, padding: '5px 8px', border: '1px solid var(--line)', background: 'var(--paper)' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>⌕</span>
          <input placeholder="搜索 因子 / 公式 / 类别"
            style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink)' }} />
        </div>
      </div>

      <div style={{ padding: '4px 0 8px', flex: 1, overflowY: 'auto', minHeight: 0 }}>
        <div className="mono" style={{ padding: '8px 16px 4px', fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.2em', display: 'flex' }}>
          <span style={{ flex: 1 }}>因子</span>
          <span style={{ width: 50, textAlign: 'right' }}>ICIR</span>
          <span style={{ width: 50, textAlign: 'right' }}>SHARPE</span>
        </div>
        {factors.map(f => {
          const active = selected === f.id;
          return (
            <div key={f.id} onClick={() => onSelect(f.id)} className="hover-row" style={{
              padding: '8px 16px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
              borderLeft: active ? '2px solid var(--yin)' : '2px solid transparent',
              background: active ? 'rgba(168,57,45,0.06)' : 'transparent',
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                  <code style={{ fontFamily: 'var(--mono)', fontSize: 12, color: active ? 'var(--ink)' : 'var(--ink-1)', fontWeight: active ? 500 : 400 }}>{f.id}</code>
                  {f.hot && <span style={{ width: 5, height: 5, background: 'var(--yin)' }} title="热门" />}
                </div>
                <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 1 }}>{f.cat}</div>
              </div>
              <div className="mono" style={{ fontSize: 11, width: 50, textAlign: 'right',
                color: Math.abs(f.icir) > 1.6 ? 'var(--yin)' : 'var(--ink-1)' }}>
                {f.icir.toFixed(2)}
              </div>
              <div className="mono" style={{ fontSize: 11, width: 50, textAlign: 'right',
                color: f.sharpe > 1.4 ? 'var(--ink)' : 'var(--ink-2)' }}>
                {f.sharpe.toFixed(2)}
              </div>
            </div>
          );
        })}
        <div style={{ padding: '14px 16px', textAlign: 'center' }}>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
            余 {meta.count - factors.length} 个未加载 · <span className="hover-pill" style={{ color: 'var(--ink-1)', cursor: 'pointer', padding: '2px 4px' }}>载入全部 →</span>
          </span>
        </div>
      </div>
    </div>
  );
}

// 因子详情主体
function FactorDetail({ factor, ic, equity, decile, picks, pool, freq, focus, onFocus }) {
  const focusPick = picks.find(p => p.code === focus) || picks[0];
  const tape = useMemo(() => genStockTape(focusPick, factor.icm < 0 ? -1 : 1), [focusPick?.code, factor.id]);
  const [showRisk, setShowRisk] = useState(false);
  const [decileFilter, setDecileFilter] = useState(null);
  return (
    <div style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: '22px 32px 40px' }}>
      {/* 标题 */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 18, paddingBottom: 16, borderBottom: '2px solid var(--ink)' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span className="seal" style={{ width: 28, height: 28, fontSize: 13 }}>α</span>
            <code style={{ fontFamily: 'var(--mono)', fontSize: 22, color: 'var(--ink)', fontWeight: 500 }}>{factor.id}</code>
            <span className="serif" style={{ fontSize: 12, padding: '1px 6px', background: 'var(--ink)', color: 'var(--paper)', letterSpacing: '0.06em' }}>{factor.cat}</span>
            <span className="serif" style={{ fontSize: 12, padding: '1px 6px', border: '1px solid var(--line)', color: 'var(--ink-2)' }}>{LIBS.find(l => l.id === factor.lib)?.name}</span>
            {factor.hot && <span className="mono" style={{ fontSize: 10, color: 'var(--yin)' }}>● 本月热门</span>}
          </div>
          <code style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink-2)', marginTop: 10, display: 'block', lineHeight: 1.7,
            padding: '7px 12px', background: 'rgba(28,24,20,0.04)', borderLeft: '2px solid var(--ink-3)' }}>{factor.formula}</code>
        </div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', textAlign: 'right', whiteSpace: 'nowrap', lineHeight: 1.9 }}>
          <div>最近回测  ·  2 分钟前</div>
          <div>样本  ·  {pool === 'hs300' ? '沪深 300' : pool === 'zz500' ? '中证 500' : pool === 'zz1000' ? '中证 1000' : '全市场'} · {freq === 'day' ? '日频' : freq === 'week' ? '周频' : '月频'}</div>
          <div>区间  ·  504 个交易日</div>
        </div>
      </div>

      {/* KPI strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 0, marginTop: 22, marginBottom: 26, border: '1px solid var(--line)' }}>
        <Kpi label="IC 均值" value={fmtPct(factor.icm)}  hint={factor.icm > 0 ? '正向相关' : '反向相关'} dir={factor.icm > 0 ? 'up' : 'down'} />
        <Kpi label="IC IR"   value={factor.icir.toFixed(2)} hint={Math.abs(factor.icir) > 1.5 ? '显著有效' : '一般'} dir={null} />
        <Kpi label="Sharpe"  value={factor.sharpe.toFixed(2)} hint="long-short" dir={null} />
        <Kpi label="年化"    value={factor.ar.toFixed(1) + '%'} hint="超额" dir="up" />
        <Kpi label="最大回撤" value={factor.mdd.toFixed(1) + '%'} hint="LS 组合" dir="down" />
        <Kpi label="换手率"   value={factor.turn + '%'} hint="单边月化" dir={null} last />
      </div>

      {/* 信号回放 · 焦点 */}
      <div style={{ marginBottom: 26 }}>
        <Panel
          title={<><span>信号回放 · </span><span className="serif" style={{ color: 'var(--ink)' }}>{focusPick.name}</span><code className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', marginLeft: 8 }}>{focusPick.code}</code><span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 6 }}>· {focusPick.industry}</span></>}
          right={<SignalLegend />}
        >
          <SignalChart tape={tape} factor={factor} />
          <SignalStats signals={tape.signals} points={tape.points} />
        </Panel>
      </div>

      {/* 两栏: 净值 + IC */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr', gap: 22, marginBottom: 26 }}>
        <Panel title="长短组合净值" right={<LegendInline />}>
          <EquityChart series={equity} />
        </Panel>
        <Panel title="月度 IC 序列" right={<span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>共 24 期 · IC|t|&gt;2 红色</span>}>
          <ICChart series={ic} />
        </Panel>
      </div>

      {/* 第二行: 十分位 + 持仓 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 22, marginBottom: 22 }}>
        <Panel title="十分位组合 · 年化超额 (%)" right={<span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{decileFilter ? `选中 D${decileFilter}` : '单调性 0.81'}</span>}>
          <DecileChart bars={decile} active={decileFilter} onToggle={(d) => setDecileFilter(decileFilter === d ? null : d)} />
        </Panel>
        <Panel title={`本期 Top 8 持仓 · ${factor.icm > 0 ? '多头' : '空头反向取多'}`} right={<span className="mono hover-pill" style={{ fontSize: 10, color: 'var(--ink-1)', cursor: 'pointer', padding: '2px 6px', border: '1px solid var(--line)' }}>导出 CSV ↧</span>}>
          <PicksTable picks={picks} focus={focusPick?.code} onFocus={onFocus} decileFilter={decileFilter} onClearFilter={() => setDecileFilter(null)} />
        </Panel>
      </div>

      {/* 风险 / 拥挤度 / 相关性 — 默认折叠 */}
      <details open={showRisk} onToggle={(e) => setShowRisk(e.currentTarget.open)} style={{ marginBottom: 8 }}>
        <summary style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderTop: '1px solid var(--line)', borderBottom: showRisk ? 'none' : '1px solid var(--line-soft)', cursor: 'pointer' }}>
          <span className="serif" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>风险归因 · 拥挤度 · 相关性</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>风格暴露 5 项 · 同库相关 4 个 · 拥挤度 62/100</span>
          <span style={{ flex: 1 }} />
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{showRisk ? '收起 ▴' : '展开 ▾'}</span>
        </summary>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 22, paddingTop: 16, paddingBottom: 8 }}>
          <Panel title="风格暴露">
            <ExposureBars rows={[
              { l: '市值',    v: -0.42, n: '小盘倾向' },
              { l: '估值',    v:  0.18, n: '略偏低估' },
              { l: '动量',    v:  0.34, n: '反转弱多头' },
              { l: '波动率', v: -0.28, n: '低波偏好' },
              { l: '盈利',    v:  0.11, n: '近中性' },
            ]} />
          </Panel>
          <Panel title="同库相关性 · |ρ| > 0.5">
            <CorrList rows={[
              { id: 'alpha#41', v:  1.00, self: true },
              { id: 'qlib#42',  v:  0.74 },
              { id: 'alpha#101',v:  0.62 },
              { id: 'gtja#88',  v: -0.58 },
              { id: 'alpha#54', v:  0.51 },
            ]} />
          </Panel>
          <Panel title="拥挤度 · 资金共识">
            <CrowdingBox />
          </Panel>
        </div>
      </details>

      <div className="mono" style={{ marginTop: 22, fontSize: 9.5, color: 'var(--ink-3)', display: 'flex', gap: 16, paddingTop: 10, borderTop: '1px solid var(--line-soft)' }}>
        <span>数据  ·  巨潮 / 同花顺 / 雪球 · ≤ 4 分钟</span>
        <span>引擎  ·  qlib 0.9.4 · cpu × 8</span>
        <span style={{ flex: 1 }} />
        <span>最近运行  ·  alpha_bench(pool=zz500,freq=month) · 14:15 · 18.4 s · ✓</span>
      </div>
    </div>
  );
}

function Kpi({ label, value, hint, dir, last }) {
  return (
    <div style={{ padding: '14px 16px', borderRight: last ? 'none' : '1px solid var(--line)', background: 'rgba(255,255,255,0.4)' }}>
      <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.18em' }}>{label}</div>
      <div className={'mono ' + (dir === 'up' ? 'up' : dir === 'down' ? 'down' : '')}
        style={{ fontSize: 20, fontWeight: 500, color: dir ? undefined : 'var(--ink)', marginTop: 5 }}>{value}</div>
      <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 3 }}>{hint}</div>
    </div>
  );
}

function Panel({ title, right, children }) {
  return (
    <div style={{ border: '1px solid var(--line)', background: 'var(--paper)' }}>
      <div style={{ padding: '10px 14px 9px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{title}</span>
        <span style={{ flex: 1 }} />
        {right}
      </div>
      <div style={{ padding: '14px 16px 16px' }}>{children}</div>
    </div>
  );
}

function fmtPct(v) {
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%';
}

// ───────── 图表 ─────────

// 共享: chart tooltip 浮层 (绝对定位 HTML overlay)
function ChartTip({ x, y, w, h, children, tipW = 168, tipH = 96 }) {
  let left = x + 14;
  let top = y - tipH - 8;
  if (left + tipW > w - 4) left = x - tipW - 14;
  if (left < 4) left = 4;
  if (top < 4) top = y + 14;
  if (top + tipH > h - 4) top = h - tipH - 4;
  return (
    <div style={{
      position: 'absolute', left, top, pointerEvents: 'none',
      background: 'var(--paper)', border: '1px solid var(--ink)',
      padding: '6px 9px', fontFamily: 'var(--sans)', fontSize: 11, color: 'var(--ink)',
      boxShadow: '3px 3px 0 -1px var(--paper-3)', zIndex: 8, minWidth: tipW - 24,
    }}>{children}</div>
  );
}

function MiniSparkline({ data, w = 60, h = 16, color = 'var(--ink)', filled = false }) {
  if (!data || data.length === 0) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const r = max - min || 1;
  const xy = (v, i) => [(i / (data.length - 1) * w), (h - ((v - min) / r) * h * 0.85 - h * 0.075)];
  const path = data.map((v, i) => { const [x, y] = xy(v, i); return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`; }).join(' ');
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {filled && <path d={path + ` L ${w} ${h} L 0 ${h} Z`} fill={color} opacity="0.12" />}
      <path d={path} stroke={color} strokeWidth="1" fill="none" />
    </svg>
  );
}

function LegendInline() {
  return (
    <div className="mono" style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 9.5, color: 'var(--ink-3)' }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 10, height: 2, background: 'var(--ink)' }} /> 多空
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 10, height: 2, background: 'var(--ink-3)' }} /> 基准
      </span>
    </div>
  );
}

function EquityChart({ series }) {
  const w = 540, h = 180, pad = { l: 36, r: 12, t: 14, b: 22 };
  const wrapperRef = useRef(null);
  const [hover, setHover] = useState(null);

  const min = Math.min(...series, 1) - 0.005;
  const max = Math.max(...series, 1) + 0.005;
  const xRange = (v) => pad.l + (w - pad.l - pad.r) * (v / (series.length - 1));
  const yRange = (v) => pad.t + (h - pad.t - pad.b) * (1 - (v - min) / (max - min));
  const path = series.map((v, i) => `${i === 0 ? 'M' : 'L'} ${xRange(i).toFixed(1)} ${yRange(v).toFixed(1)}`).join(' ');
  const fillPath = path + ` L ${xRange(series.length - 1).toFixed(1)} ${yRange(min).toFixed(1)} L ${xRange(0).toFixed(1)} ${yRange(min).toFixed(1)} Z`;
  const bench = series.map((_, i) => 1 + (i / series.length) * 0.05);
  const benchPath = bench.map((v, i) => `${i === 0 ? 'M' : 'L'} ${xRange(i).toFixed(1)} ${yRange(v).toFixed(1)}`).join(' ');
  const yTicks = 4;
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => min + (i * (max - min) / yTicks));

  const onMove = (e) => {
    const rect = wrapperRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const svgX = (px / rect.width) * w;
    if (svgX < pad.l - 4 || svgX > w - pad.r + 4) { setHover(null); return; }
    const t = (svgX - pad.l) / (w - pad.l - pad.r);
    const idx = Math.max(0, Math.min(series.length - 1, Math.round(t * (series.length - 1))));
    setHover({ idx, px, py, rectW: rect.width, rectH: rect.height });
  };

  const dayLabel = (idx) => {
    const months = ['23-06','23-08','23-10','23-12','24-02','24-04','24-06','24-08','24-10','24-12','25-02','25-05'];
    const mIdx = Math.min(months.length - 1, Math.floor(idx / series.length * months.length));
    return months[mIdx];
  };

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 190, display: 'block', cursor: 'crosshair' }}>
        {yLabels.map((v, i) => (
          <g key={i}>
            <line x1={pad.l} x2={w - pad.r} y1={yRange(v)} y2={yRange(v)} stroke="var(--line-soft)" strokeDasharray={i === 0 ? '0' : '2 3'} />
            <text x={pad.l - 6} y={yRange(v) + 3} textAnchor="end" fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)">{v.toFixed(2)}</text>
          </g>
        ))}
        <path d={fillPath} fill="var(--zhu)" opacity="0.07" />
        <path d={benchPath} stroke="var(--ink-3)" strokeWidth="1" fill="none" strokeDasharray="3 3" />
        <path d={path} stroke="var(--ink)" strokeWidth="1.4" fill="none" />
        {['23-06','23-12','24-06','24-12','25-05'].map((l, i, arr) => {
          const x = pad.l + (w - pad.l - pad.r) * (i / (arr.length - 1));
          return <text key={l} x={x} y={h - 6} fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)" textAnchor="middle">{l}</text>;
        })}
        {hover && (
          <g pointerEvents="none">
            <line x1={xRange(hover.idx)} x2={xRange(hover.idx)} y1={pad.t} y2={h - pad.b}
              stroke="var(--ink)" strokeWidth="0.6" strokeDasharray="3 3" opacity="0.55" />
            <circle cx={xRange(hover.idx)} cy={yRange(series[hover.idx])} r="3.5" fill="var(--yin)" stroke="var(--paper)" strokeWidth="1.2" />
            <circle cx={xRange(hover.idx)} cy={yRange(bench[hover.idx])} r="2.5" fill="var(--ink-3)" stroke="var(--paper)" strokeWidth="1" />
          </g>
        )}
      </svg>
      {hover && (() => {
        const v = series[hover.idx];
        const b = bench[hover.idx];
        const ex = v - b;
        return (
          <ChartTip x={hover.px} y={hover.py} w={hover.rectW} h={hover.rectH} tipW={172} tipH={92}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em', marginBottom: 4 }}>{dayLabel(hover.idx)} · 第 {hover.idx + 1} 日</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)' }}>● 多空</span>
              <span className="mono" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>{v.toFixed(4)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>┄ 基准</span>
              <span className="mono" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{b.toFixed(4)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 3, paddingTop: 3, borderTop: '1px solid var(--line-soft)', gap: 8 }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>超额</span>
              <span className={'mono ' + (ex >= 0 ? 'up' : 'down')} style={{ fontSize: 12, fontWeight: 500 }}>{ex >= 0 ? '+' : ''}{(ex * 100).toFixed(2)}%</span>
            </div>
          </ChartTip>
        );
      })()}
    </div>
  );
}

function ICChart({ series }) {
  const w = 360, h = 180, pad = { l: 30, r: 8, t: 14, b: 22 };
  const wrapperRef = useRef(null);
  const [hover, setHover] = useState(null);
  const max = Math.max(...series.map(v => Math.abs(v)), 0.07);
  const bw = (w - pad.l - pad.r) / series.length - 1;
  const mid = pad.t + (h - pad.t - pad.b) / 2;

  const onMove = (e) => {
    const rect = wrapperRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const svgX = (px / rect.width) * w;
    if (svgX < pad.l - 2 || svgX > w - pad.r + 2) { setHover(null); return; }
    const t = (svgX - pad.l) / (w - pad.l - pad.r);
    const idx = Math.max(0, Math.min(series.length - 1, Math.floor(t * series.length)));
    setHover({ idx, px, py, rectW: rect.width, rectH: rect.height });
  };

  const monthLabel = (i) => {
    const start = new Date(2023, 5);
    const d = new Date(start.getFullYear(), start.getMonth() + i);
    const y = String(d.getFullYear()).slice(2);
    const m = String(d.getMonth() + 1).padStart(2, '0');
    return `${y}-${m}`;
  };

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 190, display: 'block', cursor: 'crosshair' }}>
        <line x1={pad.l} x2={w - pad.r} y1={mid} y2={mid} stroke="var(--ink-3)" strokeWidth="1" />
        {[-0.06, -0.03, 0.03, 0.06].map((v) => {
          const y = mid - (v / max) * ((h - pad.t - pad.b) / 2);
          return (
            <g key={v}>
              <line x1={pad.l} x2={w - pad.r} y1={y} y2={y} stroke="var(--line-soft)" strokeDasharray="2 3" />
              <text x={pad.l - 4} y={y + 3} fontSize="9" textAnchor="end" fontFamily="var(--mono)" fill="var(--ink-3)">{(v*100).toFixed(0)+'%'}</text>
            </g>
          );
        })}
        {series.map((v, i) => {
          const x = pad.l + i * ((w - pad.l - pad.r) / series.length);
          const barH = (Math.abs(v) / max) * ((h - pad.t - pad.b) / 2);
          const y = v >= 0 ? mid - barH : mid;
          const sig = Math.abs(v) > 0.04;
          const isHover = hover && hover.idx === i;
          return (
            <rect key={i} x={x} y={y} width={bw} height={barH}
              fill={v >= 0 ? (sig ? 'var(--zhu)' : 'var(--zhu-soft)') : (sig ? 'var(--dai)' : 'var(--dai-soft)')}
              opacity={isHover ? 1 : (sig ? 0.95 : 0.55)}
              stroke={isHover ? 'var(--ink)' : 'none'} strokeWidth={isHover ? 0.8 : 0}
            />
          );
        })}
        {['23-06','24-06','25-05'].map((l, i, arr) => {
          const x = pad.l + (w - pad.l - pad.r) * (i / (arr.length - 1));
          return <text key={l} x={x} y={h - 6} fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)" textAnchor="middle">{l}</text>;
        })}
      </svg>
      {hover && (() => {
        const v = series[hover.idx];
        const tStat = (v / 0.014).toFixed(2); // 模拟 t-stat
        const absT = Math.abs(parseFloat(tStat));
        const stars = absT > 2.58 ? '★★★' : absT > 1.96 ? '★★' : absT > 1.65 ? '★' : '—';
        return (
          <ChartTip x={hover.px} y={hover.py} w={hover.rectW} h={hover.rectH} tipW={172} tipH={88}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em', marginBottom: 4 }}>{monthLabel(hover.idx)} · 第 {hover.idx + 1}/24 期</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)' }}>IC</span>
              <span className={'mono ' + (v >= 0 ? 'up' : 'down')} style={{ fontSize: 14, fontWeight: 500 }}>{v >= 0 ? '+' : ''}{(v * 100).toFixed(2)}%</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>t-stat</span>
              <span className="mono" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{tStat}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 2, paddingTop: 3, borderTop: '1px solid var(--line-soft)' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>显著性</span>
              <span className="mono" style={{ fontSize: 11, color: absT > 1.96 ? 'var(--yin)' : 'var(--ink-3)' }}>{stars}</span>
            </div>
          </ChartTip>
        );
      })()}
    </div>
  );
}

function DecileChart({ bars, active, onToggle }) {
  const w = 360, h = 180, pad = { l: 30, r: 8, t: 14, b: 22 };
  const wrapperRef = useRef(null);
  const [hover, setHover] = useState(null);
  const max = Math.max(...bars.map(v => Math.abs(v)));
  const bw = (w - pad.l - pad.r) / bars.length - 4;
  const mid = pad.t + (h - pad.t - pad.b) / 2;
  const barX = (i) => pad.l + 2 + i * ((w - pad.l - pad.r) / bars.length);

  const onMove = (e) => {
    const rect = wrapperRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const svgX = (px / rect.width) * w;
    const slot = (w - pad.l - pad.r) / bars.length;
    const idx = Math.floor((svgX - pad.l) / slot);
    if (idx < 0 || idx >= bars.length) { setHover(null); return; }
    setHover({ idx, px, py, rectW: rect.width, rectH: rect.height });
  };

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 190, display: 'block' }}>
        <line x1={pad.l} x2={w - pad.r} y1={mid} y2={mid} stroke="var(--ink-3)" strokeWidth="1" />
        {[-10, -5, 5, 10].map((v) => {
          const y = mid - (v / max) * ((h - pad.t - pad.b) / 2);
          return (
            <g key={v}>
              <line x1={pad.l} x2={w - pad.r} y1={y} y2={y} stroke="var(--line-soft)" strokeDasharray="2 3" />
              <text x={pad.l - 4} y={y + 3} fontSize="9" textAnchor="end" fontFamily="var(--mono)" fill="var(--ink-3)">{v}%</text>
            </g>
          );
        })}
        {bars.map((v, i) => {
          const x = barX(i);
          const barH = (Math.abs(v) / max) * ((h - pad.t - pad.b) / 2);
          const y = v >= 0 ? mid - barH : mid;
          const decileN = i + 1;
          const isActive = active === decileN;
          const isHover = hover && hover.idx === i;
          return (
            <g key={i} style={{ cursor: 'pointer' }} onClick={() => onToggle && onToggle(decileN)}>
              {/* 透明扩展点击区 */}
              <rect x={x - 2} y={pad.t} width={bw + 4} height={h - pad.t - pad.b} fill="transparent" />
              <rect x={x} y={y} width={bw} height={barH}
                fill={v >= 0 ? 'var(--zhu)' : 'var(--dai)'}
                opacity={isActive ? 1 : (isHover ? 0.95 : 0.7)}
                stroke={isActive ? 'var(--ink)' : (isHover ? 'var(--ink-2)' : 'none')}
                strokeWidth={isActive ? 1.2 : (isHover ? 0.6 : 0)}
              />
              <text x={x + bw/2} y={h - 6} fontSize="9" fontFamily="var(--mono)"
                fill={isActive ? 'var(--ink)' : 'var(--ink-3)'}
                fontWeight={isActive ? 500 : 400}
                textAnchor="middle">D{decileN}</text>
            </g>
          );
        })}
      </svg>
      {hover && (() => {
        const v = bars[hover.idx];
        const decileN = hover.idx + 1;
        const sharpe = (Math.abs(v) / 6 + 0.4).toFixed(2);
        const turnover = (90 - hover.idx * 4).toFixed(0);
        return (
          <ChartTip x={hover.px} y={hover.py} w={hover.rectW} h={hover.rectH} tipW={172} tipH={94}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em', marginBottom: 4 }}>第 {decileN} 分位 · D{decileN}</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)' }}>年化超额</span>
              <span className={'mono ' + (v >= 0 ? 'up' : 'down')} style={{ fontSize: 13, fontWeight: 500 }}>{v >= 0 ? '+' : ''}{v.toFixed(2)}%</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>夏普</span>
              <span className="mono" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{sharpe}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>换手</span>
              <span className="mono" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{turnover}%</span>
            </div>
            <div className="mono" style={{ fontSize: 9, color: 'var(--yin)', marginTop: 4, paddingTop: 3, borderTop: '1px solid var(--line-soft)', textAlign: 'center' }}>{active === decileN ? '↓ 当前已筛选' : '↓ 点击筛选 Top 8'}</div>
          </ChartTip>
        );
      })()}
    </div>
  );
}

function PicksTable({ picks, focus, onFocus, decileFilter, onClearFilter }) {
  const [sort, setSort] = useState({ col: null, dir: 'desc' });
  const [menu, setMenu] = useState(null);  // 右键菜单 {pick, x, y}
  const [hoverIdx, setHoverIdx] = useState(null);

  // 给每只票分配 decile (mock: 10 → 7)
  const enriched = picks.map((p, i) => ({ ...p, decile: 10 - Math.floor(i / 2) }));

  const filtered = decileFilter ? enriched.filter(p => p.decile === decileFilter) : enriched;
  const sorted = useMemo(() => {
    if (!sort.col) return filtered;
    return [...filtered].sort((a, b) => {
      const va = a[sort.col], vb = b[sort.col];
      const cmp = typeof va === 'number' ? va - vb : String(va).localeCompare(String(vb));
      return sort.dir === 'asc' ? cmp : -cmp;
    });
  }, [filtered, sort.col, sort.dir]);

  const toggleSort = (col) => setSort(s => s.col === col ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col, dir: 'desc' });
  const arrow = (col) => sort.col === col ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : '';

  const onCtx = (e, p) => { e.preventDefault(); setMenu({ pick: p, x: e.clientX, y: e.clientY }); };

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    setTimeout(() => window.addEventListener('click', close, { once: true }), 0);
    return () => window.removeEventListener('click', close);
  }, [menu]);

  // mock 30-day sparkline per stock
  const spark = (anchor) => Array.from({ length: 30 }, (_, i) => 1 + Math.sin(i * 0.3 + anchor * 0.1) * 0.03 + i * 0.001 * (anchor % 3 - 1));

  return (
    <div style={{ position: 'relative' }}>
      {decileFilter != null && (
        <div className="mono" style={{ fontSize: 10, color: 'var(--yin)', padding: '5px 6px', marginBottom: 4, background: 'rgba(168,57,45,0.08)', borderLeft: '2px solid var(--yin)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span>已筛选 · 第 {decileFilter} 分位 (D{decileFilter}) · {filtered.length} 只</span>
          <span style={{ flex: 1 }} />
          <span className="hover-pill" onClick={onClearFilter} style={{ cursor: 'pointer', padding: '0 5px', border: '1px solid var(--yin)' }}>清除 ×</span>
        </div>
      )}

      <div className="mono" style={{ display: 'flex', padding: '4px 4px', fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em', borderBottom: '1px solid var(--line-soft)' }}>
        <span style={{ width: 22 }}>#</span>
        <span style={{ flex: 1, cursor: 'pointer' }} onClick={() => toggleSort('name')}>个股{arrow('name')}</span>
        <span style={{ width: 30, textAlign: 'right' }}>D</span>
        <span style={{ width: 56, textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('score')}>因子值{arrow('score')}</span>
        <span style={{ width: 56, textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('weight')}>权重{arrow('weight')}</span>
        <span style={{ width: 56, textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('chg')}>本月{arrow('chg')}</span>
      </div>

      {sorted.length === 0 && (
        <div className="serif" style={{ padding: '16px 6px', textAlign: 'center', color: 'var(--ink-3)', fontSize: 12 }}>
          第 {decileFilter} 分位无 Top 8 中的个股 · 钻取全分位 →
        </div>
      )}

      {sorted.map((p, i) => {
        const active = focus === p.code;
        return (
          <div key={p.code}
            onClick={() => onFocus && onFocus(p.code)}
            onContextMenu={(e) => onCtx(e, p)}
            onMouseEnter={() => setHoverIdx(i)}
            onMouseLeave={() => setHoverIdx(null)}
            className="hover-row"
            style={{
              display: 'flex', padding: '6px 4px', alignItems: 'center',
              borderBottom: '1px solid rgba(207,196,171,0.4)',
              cursor: 'pointer',
              background: active ? 'rgba(168,57,45,0.07)' : 'transparent',
              borderLeft: active ? '2px solid var(--yin)' : '2px solid transparent',
              paddingLeft: active ? 2 : 4, position: 'relative',
            }}>
            <span className="mono" style={{ width: 22, fontSize: 10.5, color: 'var(--ink-3)' }}>{(picks.indexOf(p) + 1).toString().padStart(2, '0')}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="serif" style={{ fontSize: 12.5, color: 'var(--ink)' }}>{p.name}</div>
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{p.code} · {p.industry}</div>
            </div>
            <span className="mono" style={{ width: 30, textAlign: 'right', fontSize: 10, color: 'var(--ink-3)' }}>D{p.decile}</span>
            <span className="mono" style={{ width: 56, textAlign: 'right', fontSize: 11.5, color: 'var(--ink-1)' }}>{p.score.toFixed(3)}</span>
            <div style={{ width: 56, textAlign: 'right', display: 'flex', alignItems: 'center', gap: 5, justifyContent: 'flex-end' }}>
              <div style={{ width: 26, height: 4, background: 'var(--paper-2)' }}>
                <div style={{ width: `${p.weight * 4}%`, maxWidth: '100%', height: '100%', background: 'var(--ink-1)' }} />
              </div>
              <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>{p.weight.toFixed(1)}</span>
            </div>
            <span className={'mono ' + (p.chg >= 0 ? 'up' : 'down')} style={{ width: 56, textAlign: 'right', fontSize: 11.5 }}>{p.chg >= 0 ? '+' : ''}{p.chg.toFixed(2)}%</span>
            {hoverIdx === i && (
              <div style={{
                position: 'absolute', right: -180, top: '50%', transform: 'translateY(-50%)',
                width: 168, padding: '7px 10px', background: 'var(--paper)', border: '1px solid var(--ink)',
                boxShadow: '3px 3px 0 -1px var(--paper-3)', pointerEvents: 'none', zIndex: 7,
              }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 4 }}>
                  <span className="serif" style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 500 }}>{p.name}</span>
                  <span style={{ flex: 1 }} />
                  <span className={'mono ' + (p.chg >= 0 ? 'up' : 'down')} style={{ fontSize: 11 }}>{p.chg >= 0 ? '+' : ''}{p.chg.toFixed(2)}%</span>
                </div>
                <MiniSparkline data={spark(p.anchor || 100)} w={148} h={26} color="var(--ink)" filled />
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', display: 'flex', justifyContent: 'space-between', marginTop: 3 }}>
                  <span>近 30 日</span>
                  <span>因子值 {p.score.toFixed(2)}</span>
                </div>
              </div>
            )}
          </div>
        );
      })}
      <div className="mono" style={{ padding: '6px 4px', fontSize: 9.5, color: 'var(--ink-3)' }}>点一只查看该只股上的因子信号 ↓  ·  右键 → 更多操作</div>

      {menu && (
        <div onClick={(e) => e.stopPropagation()} style={{
          position: 'fixed', left: menu.x, top: menu.y, zIndex: 50,
          background: 'var(--paper)', border: '1px solid var(--ink)', minWidth: 168,
          boxShadow: '4px 4px 0 -1px var(--paper-3)',
        }}>
          <div className="mono" style={{ padding: '6px 10px', fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em', borderBottom: '1px solid var(--line-soft)' }}>{menu.pick.name} · {menu.pick.code}</div>
          {[
            { l: '加入自选', i: '★' },
            { l: '查看新闻', i: '📰' },
            { l: '同行对比', i: '⇄' },
            { l: '在信号回放打开', i: '⇲', a: () => { onFocus && onFocus(menu.pick.code); setMenu(null); } },
            { l: '设盯盘', i: '🔔' },
            { l: '排除该只', i: '−', warn: true },
          ].map((item, idx) => (
            <div key={idx} className="hover-row" onClick={() => { item.a && item.a(); setMenu(null); }}
              style={{ padding: '5px 10px', display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontFamily: 'var(--serif)', fontSize: 12, color: item.warn ? 'var(--yin)' : 'var(--ink-1)' }}>
              <span style={{ width: 14, opacity: 0.6, fontSize: 10 }}>{item.i}</span>
              <span>{item.l}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ExposureBars({ rows }) {
  const max = Math.max(...rows.map(r => Math.abs(r.v)), 0.5);
  return (
    <div>
      {rows.map((r, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', borderBottom: i < rows.length - 1 ? '1px solid var(--line-soft)' : 'none' }}>
          <span className="serif" style={{ width: 52, fontSize: 12, color: 'var(--ink-1)' }}>{r.l}</span>
          <div style={{ flex: 1, height: 12, position: 'relative', background: 'rgba(28,24,20,0.04)' }}>
            <div style={{ position: 'absolute', top: 0, bottom: 0, left: '50%', width: 1, background: 'var(--ink-3)' }} />
            <div style={{ position: 'absolute', top: 0, bottom: 0,
              left: r.v >= 0 ? '50%' : `${50 - Math.abs(r.v) / max * 50}%`,
              width: `${Math.abs(r.v) / max * 50}%`,
              background: r.v >= 0 ? 'var(--zhu)' : 'var(--dai)', opacity: 0.85 }} />
          </div>
          <span className={'mono ' + (r.v >= 0 ? 'up' : 'down')} style={{ width: 42, textAlign: 'right', fontSize: 11 }}>{r.v >= 0 ? '+' : ''}{r.v.toFixed(2)}</span>
          <span className="serif" style={{ width: 70, fontSize: 11, color: 'var(--ink-3)' }}>{r.n}</span>
        </div>
      ))}
    </div>
  );
}

function CorrList({ rows }) {
  return (
    <div>
      {rows.map((r, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: i < rows.length - 1 ? '1px solid var(--line-soft)' : 'none' }}>
          <code style={{ flex: 1, fontFamily: 'var(--mono)', fontSize: 12, color: r.self ? 'var(--ink-3)' : 'var(--ink-1)' }}>{r.id}{r.self && ' · 自己'}</code>
          <div style={{ flex: 1.4, height: 4, background: 'rgba(28,24,20,0.04)', position: 'relative' }}>
            <div style={{ position: 'absolute', top: 0, bottom: 0, left: '50%', width: 1, background: 'var(--ink-3)' }} />
            <div style={{ position: 'absolute', top: 0, bottom: 0,
              left: r.v >= 0 ? '50%' : `${50 - Math.abs(r.v) * 50}%`,
              width: `${Math.abs(r.v) * 50}%`,
              background: r.v >= 0 ? 'var(--ink)' : 'var(--dai)' }} />
          </div>
          <span className="mono" style={{ width: 46, textAlign: 'right', fontSize: 11, color: r.self ? 'var(--ink-3)' : 'var(--ink-1)' }}>{r.v >= 0 ? '+' : ''}{r.v.toFixed(2)}</span>
        </div>
      ))}
      <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 8, lineHeight: 1.6 }}>
        与 <code className="mono" style={{ color: 'var(--ink-1)' }}>qlib#42</code> 高度同源 (0.74), 合成时建议降权或正交化.
      </div>
    </div>
  );
}

function SignalLegend() {
  return (
    <div className="mono" style={{ display: 'flex', alignItems: 'center', gap: 14, fontSize: 9.5, color: 'var(--ink-3)' }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 10, height: 1.5, background: 'var(--ink)' }} />收盘
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <svg width="10" height="10" viewBox="0 0 10 10"><polygon points="5,1 9,9 1,9" fill="var(--zhu)" /></svg>
        买入
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <svg width="10" height="10" viewBox="0 0 10 10"><polygon points="1,1 9,1 5,9" fill="var(--dai)" /></svg>
        卖出
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 10, height: 8, background: 'rgba(168,57,45,0.10)', border: '1px dashed var(--yin)' }} />持仓区间
      </span>
    </div>
  );
}

function SignalChart({ tape, factor }) {
  const { points, signals } = tape;
  const w = 1080, h = 260, padTop = 12, padBot = 70, padL = 44, padR = 14;
  const volH = 40;
  const wrapperRef = useRef(null);
  const [hover, setHover] = useState(null);
  const [selected, setSelected] = useState(null);  // {sigIdx, kind: 'in'|'out', x, y}

  const priceMin = Math.min(...points.map(p => p.low)) * 0.99;
  const priceMax = Math.max(...points.map(p => p.high)) * 1.01;
  const innerW = w - padL - padR;
  const innerH = h - padTop - padBot;
  const x = (i) => padL + (innerW * i) / (points.length - 1);
  const y = (v) => padTop + innerH * (1 - (v - priceMin) / (priceMax - priceMin));

  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(p.close).toFixed(1)}`).join(' ');
  const bandPath =
    points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(p.high).toFixed(1)}`).join(' ')
    + ' ' +
    points.slice().reverse().map((p, idx) => {
      const i = points.length - 1 - idx;
      return `L ${x(i).toFixed(1)} ${y(p.low).toFixed(1)}`;
    }).join(' ') + ' Z';

  const yTicks = 5;
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => priceMin + (i * (priceMax - priceMin) / yTicks));
  const monthAt = [0, 30, 60, 90, 120, 150, 179].map((i) => ({
    i, x: x(i),
    label: ['24-12', '25-01', '25-02', '25-03', '25-04', '25-05', '25-05'][[0,30,60,90,120,150,179].indexOf(i)],
  }));
  const volMax = Math.max(...points.map(p => p.vol));
  const volBaseY = h - padBot + volH + 10;

  const onMove = (e) => {
    const rect = wrapperRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const svgX = (px / rect.width) * w;
    if (svgX < padL - 4 || svgX > w - padR + 4) { setHover(null); return; }
    const t = (svgX - padL) / innerW;
    const idx = Math.max(0, Math.min(points.length - 1, Math.round(t * (points.length - 1))));
    setHover({ idx, px, py, rectW: rect.width, rectH: rect.height });
  };

  const isHolding = (idx) => signals.some(s => idx >= s.in && idx <= (s.out ?? points.length - 1));
  const dayLabel = (idx) => {
    const months = ['24-12', '25-01', '25-02', '25-03', '25-04', '25-05'];
    const mIdx = Math.min(months.length - 1, Math.floor(idx / 30));
    const day = (idx % 30) + 1;
    return `${months[mIdx]}-${String(day).padStart(2, '0')}`;
  };

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 280, display: 'block', cursor: 'crosshair' }}>
        {yLabels.map((v, i) => (
          <g key={i}>
            <line x1={padL} x2={w - padR} y1={y(v)} y2={y(v)} stroke="var(--line-soft)" strokeDasharray={i === 0 || i === yLabels.length - 1 ? '0' : '2 3'} />
            <text x={padL - 6} y={y(v) + 3} fontSize="9" textAnchor="end" fontFamily="var(--mono)" fill="var(--ink-3)">{v.toFixed(1)}</text>
          </g>
        ))}

        {signals.map((s, i) => {
          const xi = x(s.in);
          const xo = x(s.out ?? points.length - 1);
          return (
            <rect key={`hold-${i}`} x={xi} y={padTop} width={xo - xi} height={innerH}
              fill="var(--yin)" fillOpacity="0.06" stroke="var(--yin)" strokeOpacity="0.35" strokeWidth="0.6" strokeDasharray="3 3" />
          );
        })}

        <path d={bandPath} fill="var(--ink)" opacity="0.05" />
        <path d={linePath} fill="none" stroke="var(--ink)" strokeWidth="1.4" />

        <line x1={padL} x2={w - padR} y1={volBaseY} y2={volBaseY} stroke="var(--line)" strokeWidth="1" />
        <text x={padL - 6} y={volBaseY + 3} fontSize="8" textAnchor="end" fontFamily="var(--mono)" fill="var(--ink-3)">VOL</text>

        {points.map((p, i) => {
          const bx = x(i);
          const bh = (p.vol / volMax) * volH;
          const isHold = isHolding(i);
          const up = i > 0 ? p.close >= points[i-1].close : true;
          return (
            <rect key={`v-${i}`} x={bx - 1.2} y={volBaseY - bh} width={2.4} height={bh}
              fill={isHold ? (up ? 'var(--zhu)' : 'var(--dai)') : 'var(--ink-3)'}
              opacity={isHold ? 0.7 : 0.4} />
          );
        })}

        {monthAt.map((m, i) => (
          <g key={i}>
            <line x1={m.x} x2={m.x} y1={padTop + innerH} y2={padTop + innerH + 4} stroke="var(--ink-3)" />
            <text x={m.x} y={padTop + innerH + 14} fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)" textAnchor="middle">{m.label}</text>
          </g>
        ))}

        {signals.map((s, i) => {
          const xi = x(s.in), yi = y(s.inPrice);
          const xo = s.out != null ? x(s.out) : null;
          const yo = s.outPrice != null ? y(s.outPrice) : null;
          const ret = s.outPrice != null ? ((s.outPrice - s.inPrice) / s.inPrice) : null;
          const inSelected = selected && selected.sigIdx === i && selected.kind === 'in';
          const outSelected = selected && selected.sigIdx === i && selected.kind === 'out';
          return (
            <g key={`sig-${i}`}>
              <line x1={xi} x2={xi} y1={yi} y2={padTop + innerH} stroke="var(--zhu)" strokeWidth="0.6" strokeDasharray="2 2" opacity="0.55" />
              <g style={{ cursor: 'pointer' }} onClick={(e) => { e.stopPropagation(); setSelected({ sigIdx: i, kind: 'in', s }); }}>
                <circle cx={xi} cy={yi - 7} r="14" fill="transparent" />
                <polygon points={`${xi},${yi-12} ${xi-6},${yi-3} ${xi+6},${yi-3}`}
                  fill="var(--zhu)" stroke={inSelected ? 'var(--ink)' : 'none'} strokeWidth={inSelected ? 1.2 : 0} />
                <text x={xi} y={yi - 16} fontSize="9" fontFamily="var(--mono)" fill="var(--zhu)" textAnchor="middle" fontWeight="500">B</text>
                <text x={xi} y={yi - 24} fontSize="8.5" fontFamily="var(--mono)" fill="var(--ink-2)" textAnchor="middle">{s.inPrice.toFixed(2)}</text>
              </g>
              {xo != null && (
                <g>
                  <line x1={xo} x2={xo} y1={padTop} y2={yo} stroke="var(--dai)" strokeWidth="0.6" strokeDasharray="2 2" opacity="0.55" />
                  <g style={{ cursor: 'pointer' }} onClick={(e) => { e.stopPropagation(); setSelected({ sigIdx: i, kind: 'out', s }); }}>
                    <circle cx={xo} cy={yo + 7} r="14" fill="transparent" />
                    <polygon points={`${xo-6},${yo+3} ${xo+6},${yo+3} ${xo},${yo+12}`}
                      fill="var(--dai)" stroke={outSelected ? 'var(--ink)' : 'none'} strokeWidth={outSelected ? 1.2 : 0} />
                    <text x={xo} y={yo + 22} fontSize="9" fontFamily="var(--mono)" fill="var(--dai)" textAnchor="middle" fontWeight="500">S</text>
                    <text x={xo} y={yo + 32} fontSize="8.5" fontFamily="var(--mono)" fill="var(--ink-2)" textAnchor="middle">{s.outPrice.toFixed(2)}</text>
                  </g>
                  <g transform={`translate(${(xi + xo) / 2}, ${padTop + 10})`}>
                    <rect x={-22} y={-7} width={44} height={14} fill={ret >= 0 ? 'var(--zhu)' : 'var(--dai)'} />
                    <text x={0} y={3} fontSize="9.5" fontFamily="var(--mono)" fill="var(--paper)" textAnchor="middle" fontWeight="500">
                      {ret >= 0 ? '+' : ''}{(ret * 100).toFixed(1)}%
                    </text>
                  </g>
                </g>
              )}
              {xo == null && (
                <g transform={`translate(${xi + 30}, ${padTop + 10})`}>
                  <rect x={-26} y={-7} width={52} height={14} fill="var(--jin)" />
                  <text x={0} y={3} fontSize="9" fontFamily="var(--mono)" fill="var(--paper)" textAnchor="middle">持仓中</text>
                </g>
              )}
            </g>
          );
        })}

        <g>
          <line x1={padL} x2={w - padR} y1={y(points[points.length-1].close)} y2={y(points[points.length-1].close)} stroke="var(--ink)" strokeWidth="0.6" strokeDasharray="4 3" opacity="0.4" />
          <rect x={w - padR - 38} y={y(points[points.length-1].close) - 7} width={38} height={14} fill="var(--ink)" />
          <text x={w - padR - 19} y={y(points[points.length-1].close) + 3} fontSize="10" fontFamily="var(--mono)" fill="var(--paper)" textAnchor="middle" fontWeight="500">
            {points[points.length-1].close.toFixed(2)}
          </text>
        </g>

        {hover && (
          <g pointerEvents="none">
            <line x1={x(hover.idx)} x2={x(hover.idx)} y1={padTop} y2={volBaseY}
              stroke="var(--ink)" strokeWidth="0.6" strokeDasharray="3 3" opacity="0.55" />
            <circle cx={x(hover.idx)} cy={y(points[hover.idx].close)} r="3.5"
              fill="var(--yin)" stroke="var(--paper)" strokeWidth="1.2" />
          </g>
        )}
      </svg>

      {hover && !selected && (() => {
        const p = points[hover.idx];
        const chg = hover.idx > 0 ? ((p.close - points[hover.idx-1].close) / points[hover.idx-1].close) : 0;
        const hold = isHolding(hover.idx);
        return (
          <ChartTip x={hover.px} y={hover.py} w={hover.rectW} h={hover.rectH} tipW={180} tipH={108}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em', marginBottom: 4 }}>{dayLabel(hover.idx)} · 第 {hover.idx + 1} 日</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)' }}>收盘</span>
              <span className="mono" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>{p.close.toFixed(2)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>日涨跌</span>
              <span className={'mono ' + (chg >= 0 ? 'up' : 'down')} style={{ fontSize: 11 }}>{chg >= 0 ? '+' : ''}{(chg * 100).toFixed(2)}%</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>量比</span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>{p.vol.toFixed(2)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 2, paddingTop: 3, borderTop: '1px solid var(--line-soft)' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>状态</span>
              <span className="mono" style={{ fontSize: 10, color: hold ? 'var(--yin)' : 'var(--ink-3)' }}>{hold ? '● 持仓中' : '○ 空仓'}</span>
            </div>
          </ChartTip>
        );
      })()}

      {selected && <SignalPopover selected={selected} onClose={() => setSelected(null)} factor={factor}
        x={(selected.kind === 'in' ? x(selected.s.in) : x(selected.s.out)) / w * (wrapperRef.current?.getBoundingClientRect().width || w)}
        y={(selected.kind === 'in' ? y(selected.s.inPrice) : y(selected.s.outPrice)) / h * (wrapperRef.current?.getBoundingClientRect().height || h)}
        wrapperW={wrapperRef.current?.getBoundingClientRect().width || w}
        wrapperH={wrapperRef.current?.getBoundingClientRect().height || h}
        dayLabel={dayLabel}
      />}
    </div>
  );
}

function SignalPopover({ selected, onClose, factor, x, y, wrapperW, wrapperH, dayLabel }) {
  const { s, kind } = selected;
  const isEntry = kind === 'in';
  const idx = isEntry ? s.in : s.out;
  const price = isEntry ? s.inPrice : s.outPrice;
  const ret = s.outPrice != null ? ((s.outPrice - s.inPrice) / s.inPrice) : null;
  const held = s.out != null ? (s.out - s.in) : null;

  const popW = 280, popH = 270;
  let left = x + 16;
  let top = y - popH / 2;
  if (left + popW > wrapperW - 4) left = x - popW - 16;
  if (left < 4) left = 4;
  if (top < 4) top = 4;
  if (top + popH > wrapperH - 4) top = wrapperH - popH - 4;

  return (
    <div onClick={(e) => e.stopPropagation()} style={{
      position: 'absolute', left, top, zIndex: 12,
      width: popW, background: 'var(--paper)', border: '1.5px solid var(--ink)',
      boxShadow: '6px 6px 0 -2px var(--paper-3)',
    }}>
      <div style={{ padding: '9px 12px 8px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{
          width: 22, height: 22, flexShrink: 0,
          background: isEntry ? 'var(--zhu)' : 'var(--dai)', color: 'var(--paper)',
          fontFamily: 'var(--serif)', fontSize: 11, fontWeight: 500,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        }}>{isEntry ? 'B' : 'S'}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="serif" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>{isEntry ? '买入信号' : '卖出信号'}</div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.1em' }}>{dayLabel(idx)} · 第 {idx + 1} 日</div>
        </div>
        <span onClick={onClose} style={{ cursor: 'pointer', color: 'var(--ink-3)', fontFamily: 'var(--mono)', fontSize: 14, padding: 4 }}>×</span>
      </div>

      <div style={{ padding: '10px 12px', borderBottom: '1px dashed var(--line)' }}>
        <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6 }}>{isEntry ? '入场条件触发' : '出场条件触发'}</div>
        <div className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)', lineHeight: 1.7 }}>
          {isEntry ? (
            <>因子 <code className="mono" style={{ color: 'var(--ink)' }}>{factor.id}</code> 值进入 <span style={{ color: 'var(--yin)' }}>top decile</span>。
            <br />vwap × √(high×low) 偏离日均线 −2.8%，量比放大至 1.6 ×。</>
          ) : (
            <>因子值跌出 top decile，<span style={{ color: 'var(--dai)' }}>触发月频换手</span>。
            <br />持仓满 {held} 个交易日；当天偏离回归至 +1.4%。</>
          )}
        </div>
      </div>

      <div style={{ padding: '10px 12px', borderBottom: '1px dashed var(--line)', display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 0 }}>
        <div>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>价格</div>
          <div className="mono" style={{ fontSize: 14, color: 'var(--ink)', fontWeight: 500, marginTop: 2 }}>{price.toFixed(2)}</div>
        </div>
        {!isEntry && ret != null && (
          <div>
            <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>本笔收益</div>
            <div className={'mono ' + (ret >= 0 ? 'up' : 'down')} style={{ fontSize: 14, fontWeight: 500, marginTop: 2 }}>{ret >= 0 ? '+' : ''}{(ret * 100).toFixed(2)}%</div>
          </div>
        )}
        {isEntry && (
          <div>
            <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>预计持仓</div>
            <div className="mono" style={{ fontSize: 14, color: 'var(--ink)', fontWeight: 500, marginTop: 2 }}>≤ 22 日</div>
          </div>
        )}
        <div>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>{isEntry ? '权重' : '持仓'}</div>
          <div className="mono" style={{ fontSize: 14, color: 'var(--ink)', fontWeight: 500, marginTop: 2 }}>{isEntry ? '12.8%' : held + ' 日'}</div>
        </div>
      </div>

      <div style={{ padding: '10px 12px' }}>
        <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6 }}>当时新闻 · 自动关联</div>
        {(isEntry ? [
          { src: '巨潮', t: '主力净流入连续 3 日, 量能放大', time: '14:32' },
          { src: '同花顺', t: '板块景气度回升, 行业排名 #4', time: '11:08' },
        ] : [
          { src: '东方财富', t: '获利盘集中兑现, 短期承压', time: '10:14' },
          { src: '雪球热度', t: '讨论度环比 −38%, 关注度回落', time: '09:22' },
        ]).map((n, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '3px 0', fontSize: 11.5 }}>
            <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 9, width: 36 }}>{n.src}</span>
            <span className="serif" style={{ color: 'var(--ink-1)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.t}</span>
            <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 9 }}>{n.time}</span>
          </div>
        ))}
      </div>

      <div style={{ padding: '8px 10px', borderTop: '1px solid var(--line)', display: 'flex', gap: 6 }}>
        <button style={{ flex: 1, padding: '5px 8px', background: 'var(--ink)', color: 'var(--paper)', border: 'none', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer' }}>看 agent 工具链 →</button>
        <button style={{ padding: '5px 10px', background: 'transparent', color: 'var(--ink-2)', border: '1px solid var(--line)', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer' }}>新闻全文</button>
      </div>
    </div>
  );
}

function SignalStats({ signals, points }) {
  const closed = signals.filter(s => s.out != null);
  const rets = closed.map(s => (s.outPrice - s.inPrice) / s.inPrice);
  const win = rets.filter(r => r > 0).length;
  const winRate = closed.length ? win / closed.length : 0;
  const avgRet = closed.length ? rets.reduce((s, r) => s + r, 0) / closed.length : 0;
  const avgHold = closed.length ? closed.reduce((s, x) => s + (x.out - x.in), 0) / closed.length : 0;
  const totalRet = rets.reduce((s, r) => s + (1 + r) - 1, 0);
  const bestRet = closed.length ? Math.max(...rets) : 0;
  const worstRet = closed.length ? Math.min(...rets) : 0;
  const open = signals.length - closed.length;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 0, marginTop: 10, border: '1px solid var(--line-soft)' }}>
      <MiniStat label="信号数" value={`${signals.length}`} hint={`${closed.length} 完成 · ${open} 持仓`} />
      <MiniStat label="胜率" value={`${(winRate * 100).toFixed(0)}%`} hint={`${win} / ${closed.length}`} />
      <MiniStat label="平均单次" value={`${avgRet >= 0 ? '+' : ''}${(avgRet * 100).toFixed(2)}%`} dir={avgRet >= 0 ? 'up' : 'down'} hint="按笔" />
      <MiniStat label="累计收益" value={`${totalRet >= 0 ? '+' : ''}${(totalRet * 100).toFixed(1)}%`} dir={totalRet >= 0 ? 'up' : 'down'} hint="本段 180 日" />
      <MiniStat label="最佳" value={`+${(bestRet * 100).toFixed(1)}%`} dir="up" hint="单次最大盈" />
      <MiniStat label="最差" value={`${(worstRet * 100).toFixed(1)}%`} dir="down" hint="单次最大亏" />
      <MiniStat label="平均持仓" value={`${avgHold.toFixed(0)} 日`} hint="月频换手" last />
    </div>
  );
}

function MiniStat({ label, value, hint, dir, last }) {
  return (
    <div style={{ padding: '8px 10px', borderRight: last ? 'none' : '1px solid var(--line-soft)' }}>
      <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>{label}</div>
      <div className={'mono ' + (dir === 'up' ? 'up' : dir === 'down' ? 'down' : '')}
        style={{ fontSize: 14, fontWeight: 500, color: dir ? undefined : 'var(--ink)', marginTop: 2 }}>{value}</div>
      <div className="serif" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 1 }}>{hint}</div>
    </div>
  );
}

function CrowdingBox() {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span className="mono" style={{ fontSize: 22, color: 'var(--ink)', fontWeight: 500 }}>62</span>
        <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)' }}>/ 100</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10, color: 'var(--jin)' }}>中等偏拥挤</span>
      </div>
      <div style={{ height: 6, background: 'var(--paper-2)', marginTop: 8, position: 'relative' }}>
        <div style={{ width: '62%', height: '100%', background: 'linear-gradient(to right, var(--dai), var(--jin), var(--zhu))' }} />
        <div style={{ position: 'absolute', top: -3, left: '62%', width: 2, height: 12, background: 'var(--ink)' }} />
      </div>
      <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: 'var(--ink-3)', marginTop: 4 }}>
        <span>0  闲置</span>
        <span>50  均衡</span>
        <span>100  极度拥挤</span>
      </div>
      <div style={{ marginTop: 12, fontFamily: 'var(--serif)', fontSize: 11.5, color: 'var(--ink-1)', lineHeight: 1.7 }}>
        近 30 日, <span className="mono" style={{ color: 'var(--ink)' }}>17 家</span> 量化产品同向持仓 top 10 中的 6 只.
        信号强度未衰减, 但需关注极端拥挤后回撤.
      </div>
    </div>
  );
}

// ───────── 入口 ─────────
function QuantApp() {
  const [selected, setSelected] = useState('alpha#41');
  const [lib, setLib] = useState('alpha101');
  const [pool, setPool] = useState('zz500');
  const [freq, setFreq] = useState('month');
  const [mode, setMode] = useState('quant');
  const [focus, setFocus] = useState('300750');
  const [userFactors, setUserFactors] = useState([]);

  const addUserFactor = useCallback((f) => {
    setUserFactors(prev => prev.find(x => x.id === f.id) ? prev.map(x => x.id === f.id ? f : x) : [f, ...prev]);
  }, []);

  // 切换库的时候, 选第一个
  useEffect(() => {
    const inLib = [...userFactors, ...FACTORS].filter(f => f.lib === lib);
    if (!inLib.find(f => f.id === selected)) {
      setSelected(inLib[0]?.id);
    }
  }, [lib]);

  return (
    <div className="paper-bg" style={{
      width: '100%', height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden',
      fontFamily: 'var(--sans)', color: 'var(--ink)', background: 'var(--paper)',
    }}>
      <TopBar mode={mode} onMode={setMode} />
      <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
        <ChatPanel selected={selected} onSelectFactor={setSelected}
          onAddFactor={(f) => { addUserFactor(f); setSelected(f.id); }}
          userFactors={userFactors} />
        <QuantWorkbench
          selected={selected} onSelect={setSelected}
          lib={lib} onLib={setLib}
          pool={pool} onPool={setPool}
          freq={freq} onFreq={setFreq}
          focus={focus} onFocus={setFocus}
          userFactors={userFactors}
        />
      </div>
    </div>
  );
}

window.QuantApp = QuantApp;
