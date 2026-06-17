// 共享组件：印章 wordmark、市况条、研究步骤等

const Brandmark = ({ subtitle, small }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: small ? 8 : 12 }}>
    <div className="seal" style={{ width: small ? 22 : 28, height: small ? 22 : 28, fontSize: small ? 14 : 16 }}>觀</div>
    <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1 }}>
      <span className="serif" style={{ fontSize: small ? 17 : 21, fontWeight: 600, letterSpacing: '0.08em', color: 'var(--ink)' }}>
        觀瀾
      </span>
      {subtitle && (
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em', marginTop: 4, textTransform: 'uppercase' }}>
          {subtitle}
        </span>
      )}
    </div>
  </div>
);

const MarketTicker = ({ items }) => (
  <div style={{ display: 'flex', gap: 28, fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink-1)' }}>
    {items.map((it, i) => (
      <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ color: 'var(--ink-2)' }}>{it.name}</span>
        <span style={{ fontWeight: 600 }}>{it.value}</span>
        {it.delta && <span className={it.delta.startsWith('-') ? 'down' : 'up'}>{it.delta}</span>}
      </div>
    ))}
  </div>
);

// 简易 sparkline / 柱形 SVG，避免引入图表库
const Sparkline = ({ data, w = 120, h = 36, up = true, fill = true }) => {
  const max = Math.max(...data), min = Math.min(...data);
  const dx = w / (data.length - 1);
  const y = (v) => h - ((v - min) / (max - min || 1)) * h;
  const points = data.map((v, i) => `${i * dx},${y(v)}`).join(' ');
  const color = up ? 'var(--zhu)' : 'var(--dai)';
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {fill && <polygon points={`0,${h} ${points} ${w},${h}`} fill={color} opacity="0.12" />}
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.4" />
    </svg>
  );
};

const Candles = ({ data, w = 360, h = 140 }) => {
  // data: [{o, c, h, l}]
  const all = data.flatMap(d => [d.h, d.l]);
  const max = Math.max(...all), min = Math.min(...all);
  const pad = 6;
  const cw = (w - pad * 2) / data.length;
  const bw = cw * 0.6;
  const y = (v) => pad + ((max - v) / (max - min || 1)) * (h - pad * 2);
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {/* 水平淡线 */}
      {[0.25, 0.5, 0.75].map(t => (
        <line key={t} x1={0} x2={w} y1={pad + t * (h - pad * 2)} y2={pad + t * (h - pad * 2)}
              stroke="var(--line-soft)" strokeDasharray="2 3" />
      ))}
      {data.map((d, i) => {
        const isUp = d.c >= d.o;
        const color = isUp ? 'var(--zhu)' : 'var(--dai)';
        const x = pad + i * cw + (cw - bw) / 2;
        const cx = pad + i * cw + cw / 2;
        const yo = y(d.o), yc = y(d.c), yh = y(d.h), yl = y(d.l);
        const top = Math.min(yo, yc), bh = Math.max(1, Math.abs(yo - yc));
        return (
          <g key={i}>
            <line x1={cx} x2={cx} y1={yh} y2={yl} stroke={color} strokeWidth="1" />
            <rect x={x} y={top} width={bw} height={bh}
                  fill={isUp ? color : color}
                  opacity={isUp ? 1 : 1} />
          </g>
        );
      })}
    </svg>
  );
};

// 研究步骤墨痕计时（亮点1）
const ResearchStep = ({ step, label, status, time }) => {
  const done = status === 'done';
  const running = status === 'running';
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, padding: '10px 0', position: 'relative' }}>
      <div style={{ position: 'relative', width: 18, flex: '0 0 18px', marginTop: 4 }}>
        <div style={{
          width: 10, height: 10, marginLeft: 4,
          background: running ? 'var(--yin)' : (done ? 'var(--ink)' : 'transparent'),
          border: done || running ? 'none' : '1px solid var(--ink-3)',
        }} />
        {running && (
          <div style={{
            position: 'absolute', inset: -3, marginLeft: 4, width: 16, height: 16,
            border: '1px solid var(--yin)', opacity: 0.4,
            animation: 'pulse 1.6s ease-in-out infinite',
          }} />
        )}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.1em' }}>{step}</span>
          <span className="serif" style={{ fontSize: 14, color: 'var(--ink)', fontWeight: 500 }}>{label}</span>
        </div>
      </div>
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', flexShrink: 0, marginTop: 6 }}>{time}</span>
    </div>
  );
};

// 数据小卡
const MetricCell = ({ label, value, delta, unit }) => (
  <div style={{ padding: '14px 0', borderRight: '1px solid var(--line-soft)', paddingRight: 20, paddingLeft: 0 }}>
    <div style={{ fontSize: 11, color: 'var(--ink-2)', marginBottom: 6, letterSpacing: '0.05em' }}>{label}</div>
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
      <span className="mono" style={{ fontSize: 22, color: 'var(--ink)', fontWeight: 500, letterSpacing: '-0.02em' }}>{value}</span>
      {unit && <span style={{ fontSize: 11, color: 'var(--ink-2)' }}>{unit}</span>}
    </div>
    {delta && (
      <div className={delta.startsWith('-') ? 'down mono' : 'up mono'} style={{ fontSize: 11, marginTop: 4 }}>
        {delta}
      </div>
    )}
  </div>
);

Object.assign(window, { Brandmark, MarketTicker, Sparkline, Candles, ResearchStep, MetricCell });
