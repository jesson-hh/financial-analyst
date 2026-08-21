// console-bench.jsx — 右栏工作台:现有页同源 iframe 原样嵌入,零新渲染。
// 驱动协议:新 artifact → GL.handoff(channel, payload) → 重载该页 iframe(?embed=1&_t=ts)
// → 页面 mount 时 take(channel) 自取自渲染。tab 只列本会话激活过的页;⌖钉住阻止自动跟随。
function WwBench({ state, workspaceId, focus, chatWide, onToggleWide, onClose, onResize, onOpenStage }) {
  const PAGES = window.WW.PAGES;
  const [tab, setTab] = React.useState(null);
  const [pinned, setPinned] = React.useState(false);
  const [srcs, setSrcs] = React.useState({});         // page → iframe src(_t 时间戳强制重载)
  // Event ids are only unique inside one conversation.  Include sid so two
  // conversations in the same research space cannot suppress each other's
  // handoff merely because both latest artifacts happen to be event #12.
  const lastRef = React.useRef('');
  const wrapRef = React.useRef(null);
  const [scale, setScale] = React.useState(1);

  // 每研究空间一个工作台:同一股票/ETF 下的多个会话共享画布、报告缓存与 handoff。
  // workspaceId 缺失时回退 sid，兼容旧后端/旧数据。
  const wsId = workspaceId || state.sid || 'rw_general';
  const wsArg = '&ws=' + encodeURIComponent(wsId);
  React.useEffect(() => {
    const arts = state.artifacts;
    if (!arts.length) { lastRef.current = String(state.sid || '') + ':'; return; }
    const a = arts[arts.length - 1];
    const sidPrefix = String(state.sid || '') + ':';
    const identity = sidPrefix + String(a.evId);
    if (identity === lastRef.current) return;
    const first = !lastRef.current.startsWith(sidPrefix);
    lastRef.current = identity;
    const drive = (art) => {
      const pg = PAGES[art.page];
      if (!pg) return;
      const assetId = art.page === 'factor' && art.payload && /^wa_[0-9a-f]{16}$/.test(String(art.payload.asset_id || ''))
        ? String(art.payload.asset_id) : '';
      if (window.GL && art.channel && !assetId) GL.handoff(art.channel, art.payload, wsId);   // 精确 asset URL 不再遗留一次性信箱
      // 仅当「本产物带 handoff(channel)」或「该页尚未载入」时才整页重载;否则(如纯调出 page_view·channel 空)
      // 不重载已载入的页——否则会把上一条产物(如选股 cfg,take 单次消费已取走)冲回 defaultCfg(掌控审计 2026-06-15)。
      setSrcs(s => (!assetId && !art.channel && s[art.page])
        ? s
        : { ...s, [art.page]: pg.file + '?embed=1' + wsArg
            + (assetId ? '&asset=' + encodeURIComponent(assetId) : '') + '&_t=' + Date.now() });
    };
    if (first) {
      const lastByPage = {};
      arts.forEach(x => { lastByPage[x.page] = x; });
      Object.values(lastByPage).forEach(drive);
    } else {
      drive(a);
    }
    if (!pinned) setTab(a.page);
  }, [state.sid, state.artifacts.length]);

  // 手动呼出(输入坞 ◫):用户显式点选 → 强制切 tab,覆盖钉住(钉住只挡 artifact 自动跟随)
  React.useEffect(() => { if (focus && focus.page) setTab(focus.page); }, [focus && focus.n]);

  // 缩放:各页 min-width 1280 → scale = clamp(w/1280, 0.6, 1)
  React.useEffect(() => {
    const fit = () => { if (wrapRef.current) setScale(Math.max(0.6, Math.min(1, wrapRef.current.clientWidth / 1280))); };
    fit(); window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, []);

  if (!state.activated.length) return null;
  const cur = (tab && state.activated.indexOf(tab) >= 0) ? tab : state.activated[state.activated.length - 1];
  let curAsset = '';
  try { curAsset = new URL(srcs[cur] || '', location.href).searchParams.get('asset') || ''; } catch (e) {}
  return (
    <div style={{ borderLeft: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--paper)', position: 'relative' }}>
      <div title="拖动调整工作台宽度"
        onPointerDown={(e) => {
          e.preventDefault();
          let last = Math.round(window.innerWidth - e.clientX);
          const move = (ev) => { last = Math.round(window.innerWidth - ev.clientX); if (onResize) onResize(last); };
          const up = () => { try { localStorage.setItem('guanlan:ww:benchw', String(last)); } catch (ex) {} window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
          window.addEventListener('pointermove', move);
          window.addEventListener('pointerup', up);
        }}
        style={{ position: 'absolute', left: -4, top: 0, bottom: 0, width: 8, cursor: 'col-resize', zIndex: 5 }} />
      <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--line)', padding: '0 8px' }}>
        {state.activated.map(p => (
          <span key={p} onClick={() => setTab(p)} style={{ padding: '10px 11px 8px', fontSize: 12, cursor: 'pointer', letterSpacing: 1, color: cur === p ? 'var(--ink)' : 'var(--ink-3)', borderBottom: '2px solid ' + (cur === p ? 'var(--yin)' : 'transparent'), marginBottom: -1, fontWeight: cur === p ? 500 : 400 }}>{PAGES[p].label}</span>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, paddingRight: 4, fontSize: 11, color: 'var(--ink-3)' }}>
          {onToggleWide && <span onClick={onToggleWide} style={{ cursor: 'pointer' }} title="切换工作台/对话宽度优先">⇋ {chatWide ? '工作台优先' : '对话优先'}</span>}
          <span onClick={() => setPinned(p => !p)} style={{ cursor: 'pointer', color: pinned ? 'var(--yin)' : 'var(--ink-3)' }} title="钉住:agent 产出新产物时不自动切换">⌖ {pinned ? '已钉住' : '钉住'}</span>
          {(cur === 'factor' || cur === 'screen') && onOpenStage && <span onClick={() => onOpenStage(curAsset ? { page: cur, asset: curAsset } : cur)} style={{ cursor: 'pointer' }} title="保留空间树，全宽进入研究台">全宽</span>}
          <span onClick={() => window.open(PAGES[cur].file + '?ws=' + encodeURIComponent(wsId) + (curAsset ? '&asset=' + encodeURIComponent(curAsset) : ''), '_blank')} style={{ cursor: 'pointer' }} title="在原独立页全宽打开">↗</span>
          <span onClick={onClose} style={{ cursor: 'pointer' }} title="收起工作台(下个产物自动滑出)">✕</span>
        </div>
      </div>
      <div ref={wrapRef} style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {state.activated.map(p => (
          <iframe key={p} src={srcs[p] || (PAGES[p].file + '?embed=1' + wsArg)} title={PAGES[p].label}
            style={{ position: 'absolute', top: 0, left: 0, border: 0, background: 'var(--paper)',
                     width: (100 / scale) + '%', height: (100 / scale) + '%',
                     transform: 'scale(' + scale + ')', transformOrigin: '0 0',
                     visibility: cur === p ? 'visible' : 'hidden' }} />
        ))}
      </div>
    </div>
  );
}
window.WwBench = WwBench;
