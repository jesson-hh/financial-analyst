// 交易盯盘台 (Trading Cockpit) — 独立全屏一级界面.
// P1: 复用 quant.jsx 定义并 window 暴露的 WatchMode (盯盘/复盘三栏), 全屏化.
//     后续 P2-P4 在此叠加 EOD 信号包先验 / 研究卡 / 市场状态条.
// 加载顺序 (见 cockpit.html): 先 quant.jsx (定义 window.WatchMode + 全部 helper),
//     再本文件 (用 window.WatchMode 定义 window.CockpitApp), 最后内联渲染 <CockpitApp/>.
const WatchMode = window.WatchMode;   // 来自 quant.jsx

const COCKPIT_API = window.GUANLAN_BACKEND || '';

// combo_pct → 信号灯颜色 (共振强度)
function _poolLight(combo) {
  if (combo == null) return 'var(--ink-4, #bbb)';
  if (combo >= 90) return '#1a7f37';     // 强共振 绿
  if (combo >= 75) return '#9a6700';     // 中 琥珀
  return 'var(--ink-3)';                 // 弱 灰
}

function _fmtPct(x) {
  return (x == null || isNaN(x)) ? '—' : Number(x).toFixed(0);
}

// 单股研究卡 (渲染 pack row 中存在的字段; 镜像 fa format_eod_prior_context)
function EodResearchCard({ row }) {
  if (!row) return null;
  const items = [];
  if (row.fm_cluster != null) items.push(['FM 簇', 'c' + row.fm_cluster]);
  if (row.combo_pct != null) items.push(['combo 分位', _fmtPct(row.combo_pct)]);
  if (row.fm_pct != null) items.push(['FM 分位', _fmtPct(row.fm_pct)]);
  if (row.lgb_rank != null) items.push(['LGB 排名', String(row.lgb_rank)]);
  if (row.v4_rating != null) items.push(['v4', String(row.v4_rating)]);
  if (row.board_total != null) items.push(['首板', String(row.board_total)]);
  if (row.mainline_state != null) items.push(['主线', String(row.mainline_state)]);
  if (row.f10_severity != null && Number(row.f10_severity) >= 1)
    items.push(['⚠负向', 'sev ' + Number(row.f10_severity)]);
  return (
    <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3,
                                   display: 'flex', flexWrap: 'wrap', gap: '2px 8px' }}>
      {items.map(([k, v], i) => (
        <span key={i}>{k} <b style={{ color: 'var(--ink-2)' }}>{v}</b></span>
      ))}
    </div>
  );
}

// 今日 EOD 盯盘池 — 消费 P2 的 GET /watch/signal_pack (rows + pool)
function EodPoolPanel() {
  const [st, setSt] = React.useState({ loading: true, rows: [], pool: [], error: null, date: null });
  const [note, setNote] = React.useState('');

  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(COCKPIT_API + '/watch/signal_pack?top_n=30');
        const j = await res.json();
        if (!alive) return;
        if (j && j.ok) {
          const date = (j.rows && j.rows[0] && j.rows[0].date) || null;
          setSt({ loading: false, rows: j.rows || [], pool: j.pool || [], error: null, date });
        } else {
          setSt({ loading: false, rows: [], pool: [], error: (j && j.reason) || '加载失败', date: null });
        }
      } catch (e) {
        if (alive) setSt({ loading: false, rows: [], pool: [], error: String(e), date: null });
      }
    })();
    return () => { alive = false; };
  }, []);

  const byCode = {};
  (st.rows || []).forEach(r => { byCode[r.code] = r; });

  async function addWatch(code) {
    setNote('');
    try {
      const res = await fetch(COCKPIT_API + '/watch/item', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ op: 'add', code }),
      });
      const j = await res.json();
      setNote(j && j.ok ? ('已加入盯盘: ' + code) : '加股失败 (先在右侧启动盯盘)');
    } catch (e) {
      setNote('加股失败: ' + e);
    }
  }

  return (
    <div style={{ width: 300, flexShrink: 0, borderRight: '1px solid var(--line)',
                  display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--paper)' }}>
      <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
        <div style={{ fontFamily: 'var(--serif)', fontSize: 13, fontWeight: 600 }}>📦 今日 EOD 盯盘池</div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 2 }}>
          {st.loading ? '加载中…'
            : st.error ? ('（' + st.error + '）')
            : ('收盘后批量 · ' + (st.date || '—') + ' · ' + (st.pool || []).length + ' 只')}
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {(!st.loading && !st.error && (st.pool || []).length === 0) && (
          <div style={{ padding: 12, fontSize: 11, color: 'var(--ink-3)' }}>
            暂无 EOD 池（需先在 research 跑 export_daily_signal_pack）
          </div>
        )}
        {(st.pool || []).map((p, i) => {
          const row = byCode[p.code];
          const combo = row ? row.combo_pct : null;
          return (
            <div key={p.code} className="hover-row" style={{
              padding: '7px 12px', borderBottom: '1px solid var(--line)', cursor: 'default' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                               background: _poolLight(combo) }} />
                <span className="mono" style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 600 }}>{p.code}</span>
                <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>#{i + 1}</span>
                <div style={{ flex: 1 }} />
                <span className="hover-pill" onClick={() => addWatch(p.code)} style={{
                  fontSize: 10, padding: '1px 7px', border: '1px solid var(--line)',
                  color: 'var(--ink-2)', fontFamily: 'var(--mono)', cursor: 'pointer' }}>+盯盘</span>
              </div>
              <EodResearchCard row={row} />
            </div>
          );
        })}
      </div>
      {note && (
        <div className="mono" style={{ padding: '6px 12px', fontSize: 10, color: 'var(--ink-2)',
                                       borderTop: '1px solid var(--line)', flexShrink: 0 }}>{note}</div>
      )}
    </div>
  );
}

// 市场状态条 (P4.1-full) — regime/涨停家数/主线 真数据源 (/watch/market_status, research EOD 算)
//   + pack 派生 (FM覆盖/强势/负向, /watch/signal_pack). 两端点独立 fetch 各自容错.
function _regimeColor(r) {
  if (r === 'bull') return '#1a7f37';
  if (r === 'bear') return '#9a1c1c';
  return 'var(--ink-2)';                          // oscillating / 未知
}
function _regimeCN(r) {
  return ({ bull: '牛市', bear: '熊市', oscillating: '震荡' })[r] || r || '—';
}
function _mlStatusCN(s) {
  return ({ mainline: '主线', initiation: '启动', revival: '二波', decay: '回调', cold: '冷门', neutral: '' })[s] || s || '';
}

function MarketStatusBar() {
  const [ms, setMs] = React.useState({ loading: true, error: null });
  const [pk, setPk] = React.useState({ loading: true });

  React.useEffect(() => {                          // 真市场级三源
    let alive = true;
    (async () => {
      try {
        const res = await fetch(COCKPIT_API + '/watch/market_status');
        const j = await res.json();
        if (!alive) return;
        if (!j || !j.ok) { setMs({ loading: false, error: (j && j.reason) || '加载失败' }); return; }
        setMs({ loading: false, error: null, data: j });
      } catch (e) { if (alive) setMs({ loading: false, error: String(e) }); }
    })();
    return () => { alive = false; };
  }, []);

  React.useEffect(() => {                          // pack 派生宽度 (同 P4.1-lite)
    let alive = true;
    (async () => {
      try {
        const res = await fetch(COCKPIT_API + '/watch/signal_pack?top_n=30');
        const j = await res.json();
        if (!alive) return;
        if (!j || !j.ok) { setPk({ loading: false }); return; }
        const rows = j.rows || [];
        setPk({
          loading: false,
          fmCov: rows.filter(r => r.fm_pct != null).length,
          strong: rows.filter(r => r.v4_score != null && Number(r.v4_score) >= 3).length,
          neg: rows.filter(r => r.f10_severity != null && Number(r.f10_severity) >= 2).length,
        });
      } catch (e) { if (alive) setPk({ loading: false }); }
    })();
    return () => { alive = false; };
  }, []);

  const cell = (label, val, color) => (
    <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
      {label} <b style={{ color: color || 'var(--ink-2)', fontSize: 12 }}>{val}</b>
    </span>
  );

  const d = ms.data || {};
  const reg = d.regime || {};
  const lu = d.limit_ups || {};
  const ml = d.mainline || {};
  const mlTop = (ml.top || []).slice(0, 3);
  const mlStale = ml.as_of && d.date && String(ml.as_of) !== String(d.date);

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '5px 16px',
                  borderBottom: '1px solid var(--line)', flexShrink: 0, background: 'rgba(28,24,20,0.02)',
                  whiteSpace: 'nowrap', overflowX: 'auto' }}>
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', flexShrink: 0 }}>📊 市场状态</span>
      {ms.loading
        ? <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>加载中…</span>
        : ms.error
          ? <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>（{ms.error}）</span>
          : <>
              {cell('EOD', d.date || '—')}
              {cell('行情', _regimeCN(reg.regime), _regimeColor(reg.regime))}
              {reg.breadth_pct != null && cell('宽度', reg.breadth_pct + '%')}
              {lu.limit_up_total != null && cell('涨停', lu.limit_up_total,
                  Number(lu.limit_up_total) >= 60 ? '#1a7f37'
                    : Number(lu.limit_up_total) <= 15 ? '#9a1c1c' : 'var(--ink-2)')}
              {lu.limit_up_total != null && (
                <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                  主{lu.limit_up_10}/双创{lu.limit_up_20} · 跌停{' '}
                  <b style={{ color: Number(lu.limit_down) >= 30 ? '#9a1c1c' : 'var(--ink-3)' }}>{lu.limit_down}</b>
                </span>
              )}
              {mlTop.length > 0 && (
                <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                  主线{' '}
                  <b style={{ color: 'var(--ink-2)' }}>
                    {mlTop.map(t => t.industry
                      + (_mlStatusCN(t.status) && t.status !== 'mainline' ? `(${_mlStatusCN(t.status)})` : '')).join(' · ')}
                  </b>
                  {mlStale && <span style={{ color: 'var(--ink-3)' }}> (as-of {ml.as_of})</span>}
                </span>
              )}
            </>}
      {!pk.loading && pk.fmCov != null && (
        <span style={{ display: 'flex', gap: 14, marginLeft: 'auto', flexShrink: 0 }}>
          {cell('FM 覆盖', pk.fmCov)}
          {cell('强势', pk.strong, pk.strong > 0 ? '#1a7f37' : 'var(--ink-3)')}
          {cell('负向', pk.neg, pk.neg > 0 ? '#9a1c1c' : 'var(--ink-3)')}
        </span>
      )}
    </div>
  );
}

function CockpitApp() {
  return (
    <div className="paper-bg" style={{
      width: '100%', height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden',
      fontFamily: 'var(--sans)', color: 'var(--ink)', background: 'var(--paper)',
    }}>
      {/* 顶部标识/导航条 (P4 扩成: regime / 涨停家数 / 主线) */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px',
        borderBottom: '1px solid var(--line)', flexShrink: 0,
      }}>
        <span style={{ fontFamily: 'var(--serif)', fontSize: 15, fontWeight: 600 }}>📡 交易盯盘台</span>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>TRADING COCKPIT · 实时信号</span>
        <div style={{ flex: 1 }} />
        <a href="quant.html" className="hover-pill" style={{
          fontSize: 11, padding: '3px 10px', border: '1px solid var(--line)',
          color: 'var(--ink-2)', fontFamily: 'var(--mono)', textDecoration: 'none',
        }}>🔬 量化工作台</a>
        <a href="index.html" className="hover-pill" style={{
          fontSize: 11, padding: '3px 10px', border: '1px solid var(--line)',
          color: 'var(--ink-2)', fontFamily: 'var(--mono)', textDecoration: 'none',
        }}>← 对话</a>
      </div>
      {/* 市场状态条 (P4.1-lite, pack 派生宽度) */}
      <MarketStatusBar />
      {/* 主体: EOD 盯盘池 (左) + WatchMode 盯盘/复盘三栏 (右) */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
        <EodPoolPanel />
        <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
          {WatchMode
            ? <WatchMode />
            : <div style={{ padding: 24, color: 'var(--ink-3)', fontFamily: 'var(--mono)', fontSize: 12 }}>
                WatchMode 未加载 — 请确认 cockpit.html 在本文件之前加载了 quant.jsx
              </div>}
        </div>
      </div>
    </div>
  );
}
window.CockpitApp = CockpitApp;
