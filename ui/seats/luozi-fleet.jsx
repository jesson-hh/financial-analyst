/* 落子 · 票池列表(TicketList,2026-07-11 三页重排)
   —— 舰队网格(FleetGrid/FleetCard/MiniCandles/FleetSignalList)退役:scanSeat 示意信号/示意净值/
      「基准截至MM-DD」陈旧对标 全删;fleetWatch 前端循环由后端 watcher 接管。
   本组件 = 「今日」页左栏:池内每票一行(名/码 · 现价/涨跌% · 最新真研判 · 盯盘开关),点行聚焦。
   红线:研判徽章只显后端落盘真 LLM 决策;报价失败显「—」;绝不合成。
   (hooks 经 luozi-chart.jsx 顶层 const { useState... } = React 全局泄漏,与 panels 同一约定) */

function TicketList({ code, onSelect, poolTick }) {
  const metas = (window.LZ_SYMBOL_META || []).slice();
  const [quotes, setQuotes] = useState({});      // {code: quote}
  const [lastDecs, setLastDecs] = useState({});  // {digitCore: {direction, ts, kind, source}}
  const [, setTick] = useState(0);               // 盯盘开关切换后强制重渲染
  // 报价轮询:7s 全池(本地后端,量级 ≤10 支;失败保留上次值不伪造)
  useEffect(() => {
    let alive = true;
    const pull = () => {
      (window.LZ_SYMBOL_META || []).forEach(m => {
        if (window.lzFetchQuote) window.lzFetchQuote(m.code).then(q => {
          if (alive && q) setQuotes(Q => Object.assign({}, Q, { [m.code]: q }));
        });
      });
    };
    pull();
    const iv = setInterval(pull, 7000);
    return () => { alive = false; clearInterval(iv); };
  }, [poolTick]);
  // 最新真研判:一次拉全量落盘决策,按数字核归组取每票最新(60s 刷新;含 watcher/手动/条件单)
  useEffect(() => {
    let alive = true;
    const dig = (x) => String(x || '').replace(/\D/g, '');
    const pull = () => {
      const API = (window.GUANLAN_BACKEND || '');
      if (!API) return;
      fetch(API + '/seats/decisions?limit=80&exclude_runs=1').then(r => r.json()).then(j => {
        if (!alive || !j || !j.ok) return;
        const by = {};
        (j.decisions || []).forEach(d => {
          const k = dig(d.code);
          if (!k || by[k]) return;   // 逆时序,首个即最新
          by[k] = { direction: d.kind === 'order' ? d.side : d.direction, ts: d.ts, kind: d.kind, source: d.source };
        });
        setLastDecs(by);
      }).catch(() => {});
    };
    pull();
    const iv = setInterval(pull, 60000);
    return () => { alive = false; clearInterval(iv); };
  }, [poolTick]);
  const dirColor = (d) => d && /买/.test(d) ? 'var(--zhu)' : (d && /卖/.test(d) ? 'var(--dai)' : 'var(--ink-2)');
  const dig = (x) => String(x || '').replace(/\D/g, '');
  return (
    <div style={{ width: 252, flexShrink: 0, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 7, padding: '11px 13px 7px' }}>
        <span className="serif" style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink-1)' }}>票池</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{metas.length} 只</span>
        <span className="mono" title="选股页「据此落子」整篮入池;动态票可移出" style={{ marginLeft: 'auto', fontSize: 8, color: 'var(--ink-3)' }}>选股可交棒</span>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {metas.map(m => {
          const q = quotes[m.code];
          const pctV = q && q.changePercent != null ? +q.changePercent : null;
          const pctColor = pctV == null ? 'var(--ink-3)' : pctV >= 0 ? 'var(--zhu)' : 'var(--dai)';
          const watched = window.lzPoolIsMonitored && window.lzPoolIsMonitored(m.code);
          const ld = lastDecs[dig(m.code)];
          const focused = m.code === code;
          return (
            <div key={m.code} onClick={() => onSelect && onSelect(m.code)} className="hover-row"
              style={{ padding: '8px 13px', cursor: 'pointer', borderBottom: '1px solid var(--line-soft)', background: focused ? 'rgba(168,57,45,0.05)' : 'transparent', borderLeft: focused ? '2px solid var(--yin)' : '2px solid transparent' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
                <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.name}</span>
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{m.code}{m.dynamic ? ' · 池' : ''}</span>
                <span onClick={(e) => { e.stopPropagation(); if (window.lzWatchSet) { window.lzWatchSet(m.code, !watched); setTick(t => t + 1); } }}
                  className="mono" title={watched ? '盯盘中(已绑策略;服务端盘中自动研判)· 点关' : '未盯 · 点开 = 绑进默认策略,服务端盘中自动研判'}
                  style={{ marginLeft: 'auto', flexShrink: 0, fontSize: 8.5, padding: '1px 7px', borderRadius: 9, cursor: 'pointer', border: '1px solid ' + (watched ? 'var(--yin)' : 'var(--line)'), color: watched ? 'var(--paper)' : 'var(--ink-3)', background: watched ? 'var(--yin)' : 'transparent', whiteSpace: 'nowrap' }}>
                  {watched ? '● 盯' : '○ 盯'}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 7, marginTop: 3 }}>
                <span className="mono num" style={{ fontSize: 12.5, fontWeight: 600, color: pctColor }}>{q && q.price != null ? q.price : '—'}</span>
                <span className="mono" style={{ fontSize: 9.5, color: pctColor }}>{pctV == null ? '' : (pctV >= 0 ? '+' : '') + pctV.toFixed(2) + '%'}</span>
                {ld && ld.direction ? (
                  <span className="mono" title={'最新' + (ld.kind === 'order' ? '条件单' : '研判') + ' · ' + String(ld.ts || '').replace('T', ' ').slice(5, 16) + (ld.source === 'watcher' ? ' · 服务端盯盘' : '')}
                    style={{ marginLeft: 'auto', fontSize: 8.5, color: dirColor(ld.direction), border: '1px solid var(--line)', borderRadius: 4, padding: '0 5px', whiteSpace: 'nowrap' }}>
                    {ld.direction}{ld.source === 'watcher' ? '·盯' : ''} {String(ld.ts || '').slice(5, 10)}
                  </span>
                ) : (
                  <span className="mono" style={{ marginLeft: 'auto', fontSize: 8.5, color: 'var(--ink-3)' }}>未研判</span>
                )}
              </div>
              {m.dynamic && focused && (
                <div style={{ marginTop: 4 }}>
                  <span onClick={(e) => { e.stopPropagation(); if (window.lzPoolRemove && window.lzPoolRemove(m.code)) { if (onSelect) onSelect(window.LZ_PRIMARY || '300750'); } }}
                    className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', border: '1px dashed var(--line)', borderRadius: 4, padding: '0 6px', cursor: 'pointer' }}>移出票池 ×</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mono" style={{ padding: '7px 13px', fontSize: 8, color: 'var(--ink-3)', lineHeight: 1.55, borderTop: '1px solid var(--line-soft)' }}>
        盯 = 绑策略,服务端盘中按节拍自动研判(须 GUANLAN_SEATS_WATCH);研判徽章 = 后端落盘真 LLM。
      </div>
    </div>
  );
}

Object.assign(window, { TicketList });
