// console-workspace-dock.jsx — 研究空间右侧 Dock。
// 总览/研究/盯盘读取真实 workspace API；文件/Agent 只读取后端能力，失败显示诚实空态。
const WW_DOCK_TABS = [
  ['overview', '总览'], ['research', '研究'], ['files', '文件'], ['agent', 'Agent'], ['watch', '盯盘'], ['memory', '记忆'],
];

function WwDockEmpty({ title, note, action, onAction }) {
  return (
    <div style={{ margin: '34px 24px', padding: '28px 20px', border: '1px dashed var(--line)', textAlign: 'center', background: 'var(--paper-2)' }}>
      <div style={{ fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--ink-1)', marginBottom: 8 }}>{title}</div>
      <div style={{ fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.7, whiteSpace: 'pre-line' }}>{note}</div>
      {action && <button onClick={onAction} style={{ marginTop: 14, border: '1px solid var(--ink-2)', background: 'var(--paper)', color: 'var(--ink)', padding: '6px 12px', cursor: 'pointer', fontFamily: 'var(--serif)' }}>{action}</button>}
    </div>
  );
}

function WwWorkspaceOverview({ workspace, sessions, onOpenStage, onBindFolder, folderNote }) {
  const root = workspace && workspace.root_path;
  const archived = !!(workspace && workspace.archived);
  const cards = [
    ['研究对象', workspace && workspace.kind === 'etf' ? 'ETF' : workspace && workspace.kind === 'stock' ? '股票' : '通用研究'],
    ['证券代码', (workspace && workspace.code) || '—'],
    ['空间会话', String(sessions.length)],
    ['Agent 模式', workspace && workspace.agent_mode === 'autonomous' ? '守护式自主' : workspace && workspace.agent_mode === 'collaborative' ? '协作写' : '只读研究'],
  ];
  return (
    <div style={{ padding: 18, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontFamily: 'var(--serif)', fontSize: 18, fontWeight: 600, letterSpacing: 1, marginBottom: 4 }}>{(workspace && (workspace.display_name || workspace.name)) || '通用研究'}</div>
      <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 10, marginBottom: 17 }}>{workspace && workspace.id}</div>
      {archived && <div style={{ marginBottom: 14, padding: '9px 10px', border: '1px solid var(--line)', background: 'var(--paper-2)', color: 'var(--ink-2)', fontSize: 10.5, lineHeight: 1.55 }}><span style={{ color: 'var(--yin)', fontWeight: 600 }}>只读历史</span> · 该研究空间已归档，研究资产、文件与运行记录仍可查看。</div>}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {cards.map(c => <div key={c[0]} style={{ border: '1px solid var(--line-soft)', padding: '11px 12px', background: 'var(--paper-2)' }}><div style={{ color: 'var(--ink-3)', fontSize: 9.5, marginBottom: 5 }}>{c[0]}</div><div style={{ fontFamily: 'var(--serif)', fontSize: 13 }}>{c[1]}</div></div>)}
      </div>
      <div style={{ marginTop: 16, paddingTop: 13, borderTop: '1px solid var(--line)' }}>
        <div style={{ fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 2, marginBottom: 7 }}>工作目录</div>
        <div title={root || ''} className="mono" style={{ fontSize: 10.5, lineHeight: 1.55, wordBreak: 'break-all', color: root ? 'var(--ink-1)' : 'var(--ink-3)' }}>{root || (workspace && workspace.root_mode === 'managed' ? '托管目录将在首次使用时准备' : '未绑定目录')}</div>
        {archived
          ? <div style={{ marginTop: 7, color: 'var(--ink-3)', fontSize: 10.5, lineHeight: 1.5 }}>归档空间保留原目录信息，不再提供目录绑定或更换。</div>
          : <React.Fragment><button onClick={onBindFolder} style={{ ...WW_DOCK_BUTTON, marginTop: 9 }}>{root ? '更换工作文件夹…' : '选择工作文件夹…'}</button>{folderNote && <div style={{ marginTop: 6, color: folderNote.ok ? 'var(--dai)' : 'var(--yin)', fontSize: 10.5, lineHeight: 1.5 }}>{folderNote.text}</div>}</React.Fragment>}
      </div>
      {!archived && <div style={{ marginTop: 18, paddingTop: 13, borderTop: '1px solid var(--line)' }}>
        <div style={{ fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 2, marginBottom: 8 }}>完整研究台</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button onClick={() => onOpenStage('screen')} style={WW_DOCK_BUTTON}>选股</button>
          <button onClick={() => onOpenStage('factor')} style={WW_DOCK_BUTTON}>因子 / 工作流</button>
        </div>
        <div style={{ color: 'var(--ink-3)', fontSize: 10.5, lineHeight: 1.55, marginTop: 7 }}>在保留左侧空间树的全宽舞台打开，可随时返回对话。</div>
      </div>}
    </div>
  );
}
const WW_DOCK_BUTTON = { border: '1px solid var(--ink-2)', background: 'var(--paper)', color: 'var(--ink)', padding: '7px 11px', cursor: 'pointer', fontFamily: 'var(--serif)', fontSize: 11.5 };

const WW_WATCH_TF = {
  day: '日线', d: '日线', '1d': '日线',
  '30': '30 分钟', '30m': '30 分钟', '30min': '30 分钟',
  '5': '5 分钟', '5m': '5 分钟', '5min': '5 分钟',
  '1': '1 分钟', '1m': '1 分钟', '1min': '1 分钟',
};
const WW_WATCH_CADENCE = {
  daily: '每日一次', day: '每日一次', hourly: '每 60 分钟', hour: '每 60 分钟',
  '60m': '每 60 分钟', '60min': '每 60 分钟', '30m': '每 30 分钟', '30min': '每 30 分钟',
  '15m': '每 15 分钟', '15min': '每 15 分钟', '10m': '每 10 分钟', '10min': '每 10 分钟', '5m': '每 5 分钟', '5min': '每 5 分钟',
  '1m': '每 1 分钟', '1min': '每 1 分钟', event: '事件触发', manual: '仅手动',
};

function wwWatchObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}
function wwWatchCode(value) {
  return window.WW && window.WW.canonicalSecurityCode
    ? window.WW.canonicalSecurityCode(value) : String(value || '').trim().toUpperCase();
}
function wwWatchTf(value) {
  const key = String(value == null ? '' : value).trim().toLowerCase();
  return WW_WATCH_TF[key] || (key ? String(value) : '—');
}
function wwWatchCadence(value) {
  const key = String(value == null ? '' : value).trim().toLowerCase();
  return WW_WATCH_CADENCE[key] || (key ? String(value) : '—');
}
function wwWatchThinking(value) {
  const key = String(value == null ? '' : value).trim().toLowerCase();
  if (key === 'pro' || key === 'deep' || key === 'reasoner') return 'Pro 深度';
  if (key === 'fast' || key === 'standard' || key === 'chat') return 'Fast 快速';
  return key ? String(value) : '—';
}
function wwWatchTime(value) {
  const raw = String(value || '');
  if (!raw) return '—';
  return raw.replace('T', ' ').replace(/Z$/, '').slice(0, 16);
}
function wwWatchNumber(value) {
  if (value == null || value === '' || typeof value === 'boolean') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function wwWatchConfidence(value) {
  const n = wwWatchNumber(value);
  if (n == null) return '';
  const pct = n >= 0 && n <= 1 ? n * 100 : n;
  return Math.round(pct) + '%';
}
function wwWatchPct(value) {
  const n = wwWatchNumber(value);
  if (n == null) return { text: '—', color: 'var(--ink-3)' };
  return { text: (n > 0 ? '+' : '') + n.toFixed(2) + '%', color: n > 0 ? 'var(--zhu)' : n < 0 ? 'var(--dai)' : 'var(--ink-3)' };
}
function wwWatchQuality(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.filter(Boolean).slice(0, 3).join(' · ');
  if (typeof value !== 'object') return String(value);
  const flags = Array.isArray(value.flags) ? value.flags : Array.isArray(value.audit_flags) ? value.audit_flags : [];
  const n = wwWatchNumber(value.n);
  const flagged = wwWatchNumber(value.quality_flagged_n);
  const complete = wwWatchNumber(value.complete_n);
  const counts = n != null
    ? ('质检 ' + (flagged != null ? Math.max(0, n - flagged) : '—') + '/' + n
      + (complete != null ? ' · 完整 ' + complete : '')) : '';
  return [value.status || value.state, value.reason || value.note, counts, ...flags.slice(0, 2)].filter(Boolean).join(' · ');
}

// 安全归一化：兼容 staged / 旧版常见信封，但永远以当前 workspace id + code
// 为边界。若后端意外返回另一空间或另一证券，前端 fail-closed，不渲染串线内容。
function wwNormalizeWorkspaceWatch(data, fallbackWorkspace) {
  const raw0 = wwWatchObject(data) || {};
  const nested = wwWatchObject(raw0.data);
  const raw = nested ? { ...raw0, ...nested } : raw0;
  const fallback = wwWatchObject(fallbackWorkspace) || {};
  const serverWorkspace = wwWatchObject(raw.workspace) || {};
  const workspace = { ...fallback, ...serverWorkspace };
  const expectedWid = String(fallback.id || '');
  const returnedWid = String(raw.workspace_id || serverWorkspace.id || '');

  let assignment = wwWatchObject(raw.assignment) || wwWatchObject(raw.watch_assignment)
    || wwWatchObject(raw.primary_assignment) || null;
  if (assignment && wwWatchObject(assignment.assignment)) assignment = assignment.assignment;
  const expectedCode = wwWatchCode(fallback.code || workspace.code);
  const returnedCode = wwWatchCode(raw.code || serverWorkspace.code || (assignment && assignment.code));
  const code = expectedCode || returnedCode;
  const scopeMismatch = !!((expectedWid && returnedWid && expectedWid !== returnedWid)
    || (expectedCode && returnedCode && expectedCode !== returnedCode)
    || (assignment && assignment.code && code && wwWatchCode(assignment.code) !== code));

  let rows = Array.isArray(raw.decisions) ? raw.decisions
    : Array.isArray(raw.items) ? raw.items
    : Array.isArray(raw.timeline) ? raw.timeline
    : raw.history && Array.isArray(raw.history.decisions) ? raw.history.decisions : [];
  const explicitLatest = wwWatchObject(raw.latest_decision) || wwWatchObject(raw.decision);
  if (explicitLatest && !rows.some(x => x && explicitLatest.id && x.id === explicitLatest.id)) rows = [explicitLatest].concat(rows);
  rows = rows.filter(x => wwWatchObject(x) && (!x.code || !code || wwWatchCode(x.code) === code)).slice();
  rows.sort((a, b) => String(b.ts || b.asof || b.created_at || '').localeCompare(String(a.ts || a.asof || a.created_at || '')));

  let snapshot = wwWatchObject(raw.latest_snapshot) || wwWatchObject(raw.snapshot)
    || wwWatchObject(raw.realtime_snapshot) || wwWatchObject(raw.latest_quote)
    || wwWatchObject(raw.quote) || null;
  if (snapshot && wwWatchObject(snapshot.snapshot)) snapshot = snapshot.snapshot;
  if (snapshot && snapshot.code && code && wwWatchCode(snapshot.code) !== code) snapshot = null;

  return {
    ok: raw.ok !== false, available: raw.available !== false, reason: raw.reason || '',
    workspace, code, assignment: scopeMismatch ? null : assignment,
    snapshot: scopeMismatch ? null : snapshot, decisions: scopeMismatch ? [] : rows,
    scopeMismatch, archived: !!(fallback.archived || workspace.archived || raw.archived || raw.read_only),
  };
}

function WwWatchMetric({ label, value, warn }) {
  return <div style={{ minWidth: 0, padding: '9px 10px', border: '1px solid var(--line-soft)', background: 'var(--paper-2)' }}>
    <div style={{ color: 'var(--ink-3)', fontSize: 9, marginBottom: 4 }}>{label}</div>
    <div title={String(value || '—')} style={{ color: warn ? 'var(--yin)' : 'var(--ink-1)', fontFamily: 'var(--serif)', fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value || '—'}</div>
  </div>;
}

function WwWatchDecision({ item, latest }) {
  const d = item || {};
  const isOrder = String(d.kind || '').toLowerCase() === 'order';
  const direction = isOrder
    ? ('条件' + (d.side || '单'))
    : (d.direction || d.hybrid_direction || d.position_action || d.action || '观望');
  const confidence = wwWatchConfidence(d.confidence);
  const tf = d.effective_evidence_tf || d.evidence_tf || d.freq || d.requested_evidence_tf;
  const rationale = d.rationale || d.reason || d.summary || d.note || '';
  const triggerText = Array.isArray(d.triggers) ? d.triggers.slice(0, 3).map(t => [t.kind, t.op, t.value].filter(x => x != null && x !== '').join(' ')).filter(Boolean).join('；') : '';
  const evidence = Array.isArray(d.key_evidence) ? d.key_evidence.slice(0, 3).join('；') : (d.key_evidence || triggerText);
  const quality = wwWatchQuality(d.data_quality || d.audit_flags);
  return <div style={{ padding: latest ? '11px 12px' : '9px 8px', border: latest ? '1px solid var(--ink-2)' : 0, borderBottom: latest ? undefined : '1px solid var(--line-soft)', background: latest ? 'var(--paper-2)' : 'transparent' }}>
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
      <span style={{ color: direction === '买入' || direction === '看多' ? 'var(--zhu)' : direction === '卖出' || direction === '看空' ? 'var(--dai)' : 'var(--ink)', fontFamily: 'var(--serif)', fontWeight: 600, fontSize: latest ? 15 : 12.5 }}>{direction}</span>
      {confidence && <span className="mono" style={{ color: 'var(--ink-2)', fontSize: 9.5 }}>置信 {confidence}</span>}
      {tf && <span className="mono" style={{ color: 'var(--yin)', fontSize: 9 }}>{wwWatchTf(tf)}</span>}
      <span className="mono" style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 8.5 }}>{wwWatchTime(d.ts || d.asof || d.created_at)}</span>
    </div>
    {rationale && <div style={{ color: 'var(--ink-2)', fontSize: 10.5, lineHeight: 1.55, marginTop: 5 }}>{rationale}</div>}
    {evidence && <div style={{ color: 'var(--ink-3)', fontSize: 9.5, lineHeight: 1.5, marginTop: 4 }}>证据：{evidence}</div>}
    <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 8.5, lineHeight: 1.45, marginTop: 4 }}>
      {[d.strategy_name, d.evidence_source, d.data_asof ? '截至 ' + wwWatchTime(d.data_asof) : '', quality].filter(Boolean).join(' · ')}
    </div>
  </div>;
}

function WwWorkspaceWatch({ workspace, data, loading, onOpenStage }) {
  const workspaceCode = wwWatchCode(workspace && workspace.code);
  if (!workspaceCode || (workspace && workspace.kind === 'general')) {
    return <WwDockEmpty title="通用研究没有单票盯盘" note="请进入已绑定证券代码的股票或 ETF 研究空间。全局策略与完整行情仍在落子中管理。" />;
  }
  if (loading) return <WwDockEmpty title="读取盯盘投影…" note="正在按当前研究空间查询落子状态与研判记录。" />;
  if (!data || data.available === false) {
    const unsupportedMarket = !!(data && data.reason === 'seats_security_code_unsupported');
    const unavailableReason = unsupportedMarket
      ? '当前落子盯盘只支持 A 股 / 北交所证券；不会用其他市场或全局记录代替。'
      : ((data && data.reason) || '当前后端还没有返回该空间的落子投影；不会用全局记录代替。');
    return <WwDockEmpty title="盯盘连接器尚未接通" note={unavailableReason} action={unsupportedMarket ? '' : '打开完整落子'} onAction={unsupportedMarket ? null : () => onOpenStage({ page: 'seats', code: workspaceCode, name: workspace && (workspace.display_name || workspace.name), focus: 'watch' })} />;
  }
  const view = wwNormalizeWorkspaceWatch(data, workspace);
  if (view.scopeMismatch) return <WwDockEmpty title="盯盘数据已拦截" note="服务返回的空间或证券代码与当前研究空间不一致，界面没有展示这些记录。" />;
  if (!view.ok) return <WwDockEmpty title="盯盘记录读取失败" note={view.reason || '服务未返回可用结果。'} />;
  const assignment = view.assignment;
  const decisions = view.decisions || [];
  const snapshot = view.snapshot;
  const enabled = !!(assignment && (assignment.enabled === true || assignment.active === true || assignment.watch_enabled === true));
  const runnable = !!(assignment && assignment.runnable !== false && !assignment.needs_review);
  const strategy = assignment && (assignment.strategy_name || (assignment.strategy && assignment.strategy.name) || assignment.strategy_id);
  const requestedTf = assignment && (assignment.requested_evidence_tf || assignment.evidence_tf || (assignment.clock && assignment.clock.execTF));
  const effectiveTf = assignment && (assignment.effective_evidence_tf || assignment.evidence_tf || requestedTf);
  const cadence = assignment && (assignment.decision_cadence || assignment.cadence || assignment.decision_freq || (assignment.clock && assignment.clock.decisionFreq));
  const thinking = assignment && (assignment.thinking_mode || assignment.mode || assignment.agent_mode);
  const latest = decisions[0] || null;
  const price = snapshot && wwWatchNumber(snapshot.price != null ? snapshot.price : snapshot.last != null ? snapshot.last : snapshot.close);
  const pct = snapshot && wwWatchNumber(snapshot.change_percent != null ? snapshot.change_percent : snapshot.changePercent != null ? snapshot.changePercent : snapshot.pct_chg);
  const minuteReturns = snapshot ? [
    ['1m', snapshot.return_1m_pct], ['5m', snapshot.return_5m_pct],
    ['15m', snapshot.return_15m_pct], ['30m', snapshot.return_30m_pct],
  ] : [];
  const dayHigh = snapshot && wwWatchNumber(snapshot.day_high != null ? snapshot.day_high : snapshot.high);
  const dayLow = snapshot && wwWatchNumber(snapshot.day_low != null ? snapshot.day_low : snapshot.low);
  const snapshotFreq = snapshot && (snapshot.freq || snapshot.effective_freq || snapshot.evidence_tf);
  const freshRaw = snapshot && String(snapshot.freshness || snapshot.status || '').toLowerCase();
  const freshness = snapshot ? (snapshot.fresh === true || freshRaw === 'fresh' ? '实时'
    : snapshot.fresh === false || freshRaw === 'stale' ? '非实时'
    : freshRaw === 'unknown' ? '新鲜度待判' : freshRaw || '快照') : '尚无快照';
  const archived = view.archived;
  const openSeats = () => {
    if (!archived) onOpenStage({ page: 'seats', code: view.code, name: workspace && (workspace.display_name || workspace.name), focus: 'watch' });
  };
  return <div style={{ height: '100%', overflowY: 'auto', padding: '14px 13px 22px', boxSizing: 'border-box' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontFamily: 'var(--serif)', fontSize: 16, fontWeight: 600 }}>{(workspace && (workspace.display_name || workspace.name)) || view.code}</div>
        <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 9.5, marginTop: 2 }}>{view.code}</div>
      </div>
      <span style={{ border: '1px solid ' + (enabled && runnable ? 'var(--dai)' : enabled ? 'var(--jin)' : 'var(--line)'), color: enabled && runnable ? 'var(--dai)' : enabled ? 'var(--jin)' : 'var(--ink-3)', padding: '3px 7px', fontSize: 9.5 }}>{enabled ? (runnable ? '● 已盯盘' : '△ 待复核') : assignment ? '○ 已暂停' : '○ 未配置'}</span>
    </div>
    {archived && <div style={{ marginTop: 10, padding: '8px 9px', border: '1px solid var(--line)', background: 'var(--paper-2)', color: 'var(--ink-2)', fontSize: 10.5, lineHeight: 1.5 }}><span style={{ color: 'var(--yin)', fontWeight: 600 }}>只读历史</span> · 归档空间只展示既有配置与研判，不提供落子执行入口。</div>}
    {assignment && (assignment.needs_review || (enabled && !runnable)) && <div style={{ marginTop: 9, padding: '7px 9px', borderLeft: '2px solid var(--jin)', background: 'var(--paper-2)', color: 'var(--ink-2)', fontSize: 10 }}>当前盯盘配置需要复核后再运行{assignment.status ? ' · ' + assignment.status : ''}。</div>}
    <div style={{ marginTop: 13, fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: 1.8 }}>当前盯盘</div>
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginTop: 7 }}>
      <WwWatchMetric label="主策略" value={strategy || '尚未选择'} />
      <WwWatchMetric label="真实证据" value={wwWatchTf(effectiveTf)} warn={!!(requestedTf && effectiveTf && wwWatchTf(requestedTf) !== wwWatchTf(effectiveTf))} />
      <WwWatchMetric label="判断节拍" value={wwWatchCadence(cadence)} />
      <WwWatchMetric label="思考模式" value={wwWatchThinking(thinking)} />
    </div>
    {requestedTf && effectiveTf && wwWatchTf(requestedTf) !== wwWatchTf(effectiveTf) && <div style={{ color: 'var(--yin)', fontSize: 9.5, marginTop: 6 }}>请求 {wwWatchTf(requestedTf)}，本次实际使用 {wwWatchTf(effectiveTf)}</div>}

    <div style={{ marginTop: 15, paddingTop: 11, borderTop: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline' }}><span style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: 1.8 }}>最新行情快照</span><span className="mono" style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 8.5 }}>{snapshot ? wwWatchTime(snapshot.data_asof || snapshot.asof || snapshot.ts || snapshot.updated_at) : '—'}</span></div>
      {snapshot ? <div style={{ marginTop: 7, display: 'flex', alignItems: 'baseline', gap: 9 }}>
        <span className="mono" style={{ fontSize: 18, color: 'var(--ink)' }}>{Number.isFinite(price) ? price.toFixed(price >= 100 ? 2 : 3).replace(/0+$/, '').replace(/\.$/, '') : '—'}</span>
        {Number.isFinite(pct) && <span className="mono" style={{ color: pct > 0 ? 'var(--zhu)' : pct < 0 ? 'var(--dai)' : 'var(--ink-3)', fontSize: 11 }}>{pct > 0 ? '+' : ''}{pct.toFixed(2)}%</span>}
        <span style={{ marginLeft: 'auto', color: snapshot.fresh === true ? 'var(--dai)' : 'var(--ink-3)', fontSize: 9.5 }}>{freshness}</span>
      </div> : <div style={{ color: 'var(--ink-3)', fontSize: 10.5, lineHeight: 1.5, marginTop: 7 }}>服务尚未返回价格快照；下方研判仍按其数据截至时间展示。</div>}
      {snapshot && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4, marginTop: 8 }}>
        {minuteReturns.map(row => { const p = wwWatchPct(row[1]); return <div key={row[0]} style={{ padding: '5px 3px', textAlign: 'center', border: '1px solid var(--line-soft)', background: 'var(--paper-2)' }}><div className="mono" style={{ color: 'var(--ink-3)', fontSize: 8 }}>{row[0]}</div><div className="mono" style={{ color: p.color, fontSize: 9.5, marginTop: 2 }}>{p.text}</div></div>; })}
      </div>}
      {snapshot && <div className="mono" style={{ display: 'flex', gap: 8, color: 'var(--ink-3)', fontSize: 8.5, marginTop: 5 }}>
        <span>日高 {Number.isFinite(dayHigh) ? dayHigh : '—'}</span><span>日低 {Number.isFinite(dayLow) ? dayLow : '—'}</span><span style={{ marginLeft: 'auto' }}>实际 {wwWatchTf(snapshotFreq)}</span>
      </div>}
      {snapshot && <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 8.5, marginTop: 4 }}>{[snapshot.source || snapshot.evidence_source, wwWatchQuality(snapshot.data_quality)].filter(Boolean).join(' · ')}</div>}
    </div>

    <div style={{ marginTop: 15, paddingTop: 11, borderTop: '1px solid var(--line)' }}>
      <div style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: 1.8, marginBottom: 7 }}>最新判断</div>
      {latest ? <WwWatchDecision item={latest} latest /> : <div style={{ color: 'var(--ink-3)', fontSize: 10.5, padding: '10px 0' }}>暂无研判——启动本票盯盘后，真实 Agent 判断会出现在这里。</div>}
    </div>

    <div style={{ marginTop: 15, paddingTop: 11, borderTop: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: 3 }}><span style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: 1.8 }}>判断时间线</span><span className="mono" style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 8.5 }}>{decisions.length} 条</span></div>
      {decisions.slice(1).map((d, i) => <WwWatchDecision key={d.id || d.data_digest || ((d.ts || d.asof || '') + ':' + i)} item={d} />)}
      {decisions.length <= 1 && <div style={{ color: 'var(--ink-3)', fontSize: 10, padding: '8px 0' }}>尚无更早记录。</div>}
    </div>

    <button type="button" onClick={openSeats} disabled={archived} style={{ ...WW_DOCK_BUTTON, width: '100%', marginTop: 15, opacity: archived ? .5 : 1, cursor: archived ? 'not-allowed' : 'pointer' }}>{archived ? '归档空间仅供查看' : '在完整落子中打开 →'}</button>
    {!archived && <div style={{ color: 'var(--ink-3)', fontSize: 9.5, lineHeight: 1.5, marginTop: 6, textAlign: 'center' }}>完整 K 线、策略编辑与条件单留在落子；这里仅展示当前空间投影。</div>}
  </div>;
}

const WW_DOCK_DOCUMENT_TYPES = Object.freeze({
  'application/pdf': ['.pdf', 'PDF'],
  'text/markdown': ['.md|.markdown', 'Markdown'],
  'text/plain': ['.txt', '文本'],
  'text/csv': ['.csv', 'CSV'],
  'text/tab-separated-values': ['.tsv', 'TSV'],
  'application/json': ['.json', 'JSON'],
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx', 'Excel'],
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx', 'Word'],
});
const WW_DOCK_IMAGE_TYPES = Object.freeze({
  'image/png': true, 'image/jpeg': true, 'image/webp': true,
});

function wwDockAssetText(value, fallback, limit) {
  const text = String(value || '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim();
  return (text || fallback || '').slice(0, limit);
}

function wwDockAttachmentName(value, fallback) {
  if (typeof value !== 'string' || !value || /[\u0000-\u001f\u007f\\/:]/.test(value)) return '';
  return wwDockAssetText(value, fallback, 160);
}

function wwDockHasRawAddress(row) {
  return ['path', 'url', 'src', 'href', 'file', 'blob_name'].some(key => Object.prototype.hasOwnProperty.call(row, key));
}

// 重新打开报告时只消费后端签发的 opaque 附件 ID；图片只进入 <img>，
// 文件只进入 download 链接，任何 path/url/src 均直接拦截。
function wwNormalizeDockReportAsset(response, workspaceId, requestedAssetId) {
  const envelope = response && typeof response === 'object' ? response : {};
  const asset = envelope.asset && typeof envelope.asset === 'object' ? envelope.asset : null;
  if (envelope.ok === false || !asset) return { ok: false, reason: envelope.reason || '研究资产读取失败' };
  if (!/^wa_[0-9a-f]{16}$/.test(String(requestedAssetId || ''))
      || asset.id !== requestedAssetId || asset.workspace_id !== workspaceId
      || asset.scope !== 'workspace' || !['report', 'analysis'].includes(String(asset.kind || ''))) {
    return { ok: false, reason: '研究资产范围校验失败，内容未展示' };
  }
  const attachments = asset.attachments && typeof asset.attachments === 'object'
    && !Array.isArray(asset.attachments) ? asset.attachments : {};
  const rawImages = Array.isArray(attachments.images) ? attachments.images.slice(0, 12) : [];
  const rawDocuments = Array.isArray(attachments.documents) ? attachments.documents.slice(0, 12) : [];
  const images = rawImages.map(row => {
    if (!row || typeof row !== 'object' || Array.isArray(row) || wwDockHasRawAddress(row)) return null;
    const mediaId = String(row.media_id || '');
    const mime = String(row.mime || '').trim().toLowerCase();
    const name = wwDockAttachmentName(row.name, '分析图片');
    if (!/^mi_[0-9a-f]{32}$/.test(mediaId) || row.workspace_id !== workspaceId
        || !WW_DOCK_IMAGE_TYPES[mime] || !name) return null;
    const dimension = value => {
      const n = Number(value);
      return Number.isInteger(n) && n > 0 && n <= 100000 ? n : null;
    };
    return {
      mediaId, mime, name, alt: wwDockAssetText(row.alt, name, 300),
      width: dimension(row.width), height: dimension(row.height),
    };
  }).filter(Boolean);
  const documents = rawDocuments.map(row => {
    if (!row || typeof row !== 'object' || Array.isArray(row) || wwDockHasRawAddress(row)) return null;
    const documentId = String(row.document_id || '');
    const mime = String(row.mime || '').trim().toLowerCase();
    const spec = WW_DOCK_DOCUMENT_TYPES[mime];
    const name = wwDockAttachmentName(row.name, '分析文件');
    const size = Number(row.size);
    const suffix = name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '';
    if (!/^do_[0-9a-f]{32}$/.test(documentId) || row.workspace_id !== workspaceId
        || !spec || spec[0].split('|').indexOf(suffix) < 0 || !Number.isInteger(size) || size <= 0
        || size > 10 * 1024 * 1024) return null;
    return {
      documentId, mime, name, size, typeLabel: spec[1],
      title: wwDockAssetText(row.title, name, 300),
    };
  }).filter(Boolean);
  return {
    ok: true,
    id: asset.id,
    title: wwDockAssetText(asset.name, '分析报告', 160),
    status: wwDockAssetText(asset.status, 'unknown', 40),
    summary: wwDockAssetText(asset.summary, '', 600),
    updated: wwDockAssetText(asset.updated || asset.created, '', 40),
    codeSha256: /^[0-9a-f]{64}$/.test(String(asset.code_sha256 || '')) ? asset.code_sha256 : '',
    inputSha256: /^[0-9a-f]{64}$/.test(String(asset.input_sha256 || '')) ? asset.input_sha256 : '',
    images,
    documents,
    rejected: (rawImages.length - images.length) + (rawDocuments.length - documents.length),
  };
}

function wwDockFileSize(value) {
  if (value < 1024) return value + ' B';
  if (value < 1024 * 1024) return (value / 1024).toFixed(value < 10 * 1024 ? 1 : 0) + ' KB';
  return (value / (1024 * 1024)).toFixed(1) + ' MB';
}

function WwWorkspaceReportAsset({ workspaceId, assetId, onBack }) {
  const [view, setView] = React.useState(null);
  React.useEffect(() => {
    let dead = false;
    setView(null);
    if (!/^wa_[0-9a-f]{16}$/.test(String(assetId || ''))) {
      setView({ ok: false, reason: '研究资产编号无效' });
      return () => { dead = true; };
    }
    Promise.resolve().then(() => window.WW.workspaceAsset(workspaceId, assetId))
      .then(result => { if (!dead) setView(wwNormalizeDockReportAsset(result, workspaceId, assetId)); })
      .catch(error => { if (!dead) setView({ ok: false, reason: (error && error.message) || '研究资产读取失败' }); });
    return () => { dead = true; };
  }, [workspaceId, assetId]);
  const images = view && view.ok ? view.images : [];
  const documents = view && view.ok ? view.documents : [];
  return <div style={{ height: '100%', overflowY: 'auto', padding: '12px 13px 22px', boxSizing: 'border-box' }}>
    <button type="button" onClick={onBack} style={{ border: 0, background: 'transparent', color: 'var(--ink)', cursor: 'pointer', padding: '2px 0 9px', fontFamily: 'var(--serif)' }}>← 返回研究资产</button>
    {!view && <WwDockEmpty title="读取分析产物…" note="正在按当前研究空间重新校验历史附件。" />}
    {view && !view.ok && <WwDockEmpty title="无法打开研究资产" note={view.reason || '历史附件不可用。'} />}
    {view && view.ok && <React.Fragment>
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: 12 }}>
        <div style={{ fontFamily: 'var(--serif)', fontSize: 16, fontWeight: 600, lineHeight: 1.4, overflowWrap: 'anywhere' }}>{view.title}</div>
        <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 9, marginTop: 5 }}>{view.id}{view.updated ? ' · ' + view.updated.slice(0, 16) : ''}</div>
        <div role="status" data-report-status={view.status} style={{ color: 'var(--yin)', fontSize: 11, lineHeight: 1.6, marginTop: 7 }}>资产状态：{view.status} · 文件已归档不代表质量门通过</div>
        {view.summary && <div style={{ color: 'var(--ink-2)', fontSize: 10.5, lineHeight: 1.6, marginTop: 4 }}>{view.summary}</div>}
        {(view.codeSha256 || view.inputSha256) && <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 8.5, lineHeight: 1.5, marginTop: 5, overflowWrap: 'anywhere' }}>{view.codeSha256 && '代码 ' + view.codeSha256.slice(0, 12)}{view.codeSha256 && view.inputSha256 ? ' · ' : ''}{view.inputSha256 && '输入 ' + view.inputSha256.slice(0, 12)}</div>}
      </div>
      {!!images.length && <section aria-label="报告图片" style={{ marginTop: 15 }}>
        <div style={{ color: 'var(--ink-3)', fontSize: 9.5, letterSpacing: 1.8, marginBottom: 7 }}>图片 · {images.length}</div>
        {images.map(image => {
          const url = window.WW.mediaUrl(image.mediaId);
          return <figure key={image.mediaId} style={{ margin: '0 0 9px', padding: 7, border: '1px solid var(--line-soft)', background: 'var(--paper-2)' }}>
            <img src={url} alt={image.alt} width={image.width || undefined} height={image.height || undefined} loading="lazy" style={{ display: 'block', maxWidth: '100%', width: 'auto', height: 'auto', maxHeight: 320, objectFit: 'contain', margin: '0 auto', background: 'var(--paper)' }} />
            <figcaption style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 7, fontSize: 10.5 }}><span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{image.name}</span><a href={url} download={image.name} style={{ color: 'var(--yin)', textDecoration: 'none', flexShrink: 0 }}>下载 ↓</a></figcaption>
          </figure>;
        })}
      </section>}
      {!!documents.length && <section aria-label="报告文件" style={{ marginTop: 15 }}>
        <div style={{ color: 'var(--ink-3)', fontSize: 9.5, letterSpacing: 1.8, marginBottom: 7 }}>文件 · {documents.length} · 仅下载</div>
        {documents.map(document => {
          const url = window.WW.documentUrl(document.documentId);
          return <a key={document.documentId} href={url} download={document.name} aria-label={'下载文件：' + document.title} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 8px', borderBottom: '1px solid var(--line-soft)', color: 'var(--ink)', textDecoration: 'none' }}>
            <span className="serif" style={{ flex: '0 0 42px', color: 'var(--yin)', fontSize: 10 }}>{document.typeLabel}</span>
            <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere', fontSize: 11.5 }}>{document.title}</span>
            <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 8.5, whiteSpace: 'nowrap' }}>{wwDockFileSize(document.size)} ↓</span>
          </a>;
        })}
      </section>}
      {!images.length && !documents.length && <WwDockEmpty title="历史附件不可用" note="报告记录仍在，但没有通过当前安全校验的图片或文件。" />}
      {view.rejected > 0 && <div role="status" style={{ marginTop: 10, color: 'var(--yin)', fontSize: 9.5, lineHeight: 1.5 }}>已拦截 {view.rejected} 个不合规或跨空间附件。</div>}
    </React.Fragment>}
  </div>;
}

function wwNormalizeIndustryReviewPayload(raw, workspaceId, assetId) {
  if (!raw || raw.ok === false) return { ok: false, reason: (raw && (raw.reason || raw.content)) || '行业资讯候选读取失败', reasonCode: raw && raw.reason_code, httpStatus: raw && raw.http_status };
  const asset = raw.asset && typeof raw.asset === 'object' ? raw.asset : {};
  if (asset.id !== assetId || asset.workspace_id !== workspaceId || asset.dataset_id !== 'research.newsradar.live') {
    return { ok: false, reason: '行业资讯审核快照范围校验失败' };
  }
  const state = raw.review_state && typeof raw.review_state === 'object' ? raw.review_state : {};
  const revision = Number(state.revision);
  if (!Number.isInteger(revision) || revision < 0) return { ok: false, reason: '行业资讯审核 revision 无效' };
  const candidates = (Array.isArray(raw.candidates) ? raw.candidates : []).map(row => {
    if (!row || typeof row !== 'object' || !/^INC-[0-9A-F]{16}$/.test(String(row.candidate_id || ''))) return null;
    const current = ['related', 'unrelated', 'uncertain'].includes(String(row.decision || '')) ? row.decision : 'unreviewed';
    return {
      id: String(row.candidate_id), title: wwDockAssetText(row.title, '未命名资讯', 260),
      source: wwDockAssetText(row.source, '未知来源', 100), sector: wwDockAssetText(row.sector, '', 40),
      sourceTime: wwDockAssetText(row.source_time, '', 64), decision: current,
      reportEligible: row.report_eligible === true, issuerFact: row.issuer_fact === true,
    };
  }).filter(row => row && !row.issuerFact);
  return { ok: true, revision, candidates, counts: raw.counts || {} };
}

function WwIndustryReviewPanel({ workspaceId, sessionId, assets }) {
  const snapshot = (Array.isArray(assets) ? assets : []).find(row => row && row.kind === 'data' && row.dataset_id === 'research.newsradar.live');
  const assetId = snapshot && /^wa_[0-9a-f]{16}$/.test(String(snapshot.id || '')) ? String(snapshot.id) : '';
  const [view, setView] = React.useState(null);
  const [selected, setSelected] = React.useState({});
  const [decision, setDecision] = React.useState('related');
  const [busy, setBusy] = React.useState(false);
  const [notice, setNotice] = React.useState(null);
  const [reload, setReload] = React.useState(0);
  React.useEffect(() => {
    let dead = false;
    setView(null); setSelected({}); setNotice(null);
    if (!assetId || !sessionId) return () => { dead = true; };
    Promise.resolve(window.WW.workspaceIndustryReviews(workspaceId, sessionId, assetId, 100))
      .then(raw => { if (!dead) setView(wwNormalizeIndustryReviewPayload(raw, workspaceId, assetId)); })
      .catch(error => { if (!dead) setView({ ok: false, reason: (error && error.message) || '行业资讯候选读取失败' }); });
    return () => { dead = true; };
  }, [workspaceId, sessionId, assetId, reload]);
  if (!assetId) return null;
  const chosen = view && view.ok ? view.candidates.filter(row => selected[row.id]) : [];
  const submit = async () => {
    if (!chosen.length || busy) return;
    const label = decision === 'related' ? '相关' : decision === 'unrelated' ? '不相关' : '不确定';
    if (!window.confirm('确认将选中的 ' + chosen.length + ' 条行业资讯标记为“' + label + '”？\n仅 related 可作为行业背景，始终不会升级为公司事实。')) return;
    setBusy(true); setNotice(null);
    const key = 'dock-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
    const raw = await window.WW.workspaceIndustryReviewSubmit(workspaceId, {
      session_id: sessionId, snapshot_asset_id: assetId, expected_revision: view.revision,
      idempotency_key: key, reviews: chosen.map(row => ({ candidate_id: row.id, decision })),
      confirmed: true,
    });
    if (!raw || raw.ok === false) {
      const conflict = raw && (raw.http_status === 409 || raw.reason_code === 'industry_review_revision_conflict');
      setNotice({ ok: false, conflict, text: conflict ? '审核版本冲突：其他会话已更新账本。当前选择未提交，请重新加载后再确认。' : ((raw && (raw.reason || raw.content)) || '批量审核失败') });
      setBusy(false); return;
    }
    const endRevision = raw.review_state && Number(raw.review_state.revision);
    setNotice({ ok: true, text: '已原子提交 ' + chosen.length + ' 条' + (Number.isInteger(endRevision) ? ' · revision ' + endRevision : '') });
    setBusy(false); setReload(value => value + 1);
  };
  return <section aria-label="行业资讯批量审核" style={{ margin: '0 0 13px', padding: 10, border: '1px solid var(--line)', background: 'var(--paper-2)' }}>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><strong className="serif" style={{ flex: 1, fontSize: 12 }}>行业资讯审核</strong><span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{view && view.ok ? 'revision ' + view.revision : ''}</span><button type="button" disabled={busy} onClick={() => setReload(value => value + 1)} style={{ border: 0, background: 'transparent', color: 'var(--yin)', cursor: 'pointer', fontSize: 10 }}>重新加载</button></div>
    <div style={{ color: 'var(--ink-3)', fontSize: 9.5, lineHeight: 1.5, marginTop: 4 }}>同赛道资讯仅为行业候选。多选后统一标记；只有“相关”进入下一份研报的行业背景。</div>
    {!view && <div className="mono" style={{ padding: '9px 0', fontSize: 9.5 }}>读取候选…</div>}
    {view && !view.ok && <div role="alert" style={{ padding: '8px 0 2px', color: 'var(--yin)', fontSize: 10 }}>{view.reason}</div>}
    {view && view.ok && <React.Fragment>
      <div style={{ display: 'flex', gap: 5, marginTop: 8 }}>{[['related', '相关'], ['unrelated', '不相关'], ['uncertain', '不确定']].map(pair => <button type="button" key={pair[0]} aria-pressed={decision === pair[0]} onClick={() => setDecision(pair[0])} style={{ border: '1px solid ' + (decision === pair[0] ? 'var(--yin)' : 'var(--line)'), color: decision === pair[0] ? 'var(--yin)' : 'var(--ink-2)', background: 'var(--paper)', padding: '4px 7px', cursor: 'pointer', fontSize: 9.5 }}>{pair[1]}</button>)}</div>
      <div style={{ maxHeight: 250, overflowY: 'auto', marginTop: 7, borderTop: '1px solid var(--line-soft)' }}>{view.candidates.map(row => <label key={row.id} style={{ display: 'grid', gridTemplateColumns: '18px 1fr auto', gap: 5, alignItems: 'start', padding: '7px 2px', borderBottom: '1px solid var(--line-soft)', cursor: 'pointer' }}>
        <input type="checkbox" checked={!!selected[row.id]} onChange={event => setSelected(old => ({ ...old, [row.id]: event.target.checked }))} />
        <span style={{ minWidth: 0 }}><span style={{ display: 'block', fontSize: 10.5, lineHeight: 1.4, overflowWrap: 'anywhere' }}>{row.title}</span><span className="mono" style={{ display: 'block', color: 'var(--ink-3)', fontSize: 8.5, marginTop: 2 }}>{row.source}{row.sector ? ' · ' + row.sector : ''}{row.sourceTime ? ' · ' + row.sourceTime : ''}</span></span>
        <span className="mono" style={{ fontSize: 8.5, color: row.decision === 'related' ? 'var(--dai)' : 'var(--ink-3)' }}>{row.decision}</span>
      </label>)}</div>
      {!view.candidates.length && <div className="mono" style={{ padding: '9px 0', color: 'var(--ink-3)', fontSize: 9.5 }}>该快照没有可审核候选</div>}
      <button type="button" disabled={!chosen.length || busy} onClick={submit} style={{ width: '100%', marginTop: 8, border: 0, padding: '7px 9px', background: chosen.length && !busy ? 'var(--ink)' : 'var(--line)', color: 'var(--paper)', cursor: chosen.length && !busy ? 'pointer' : 'default', fontSize: 10 }}>{busy ? '提交中…' : '提交 ' + chosen.length + ' 条原子审核'}</button>
      {notice && <div role={notice.ok ? 'status' : 'alert'} data-review-conflict={notice.conflict ? 'true' : 'false'} style={{ marginTop: 7, color: notice.ok ? 'var(--dai)' : 'var(--yin)', fontSize: 9.5, lineHeight: 1.5 }}>{notice.text}</div>}
    </React.Fragment>}
  </section>;
}

function WwWorkspaceResearch({ workspaceId, sessionId, data, loading, onOpenStage }) {
  const [reportAsset, setReportAsset] = React.useState(null);
  const reportAssetId = reportAsset && reportAsset.workspaceId === workspaceId ? reportAsset.id : '';
  if (reportAssetId) return <WwWorkspaceReportAsset workspaceId={workspaceId} assetId={reportAssetId} onBack={() => setReportAsset(null)} />;
  if (loading) return <WwDockEmpty title="读取研究资产…" note="正在查询该空间的真实产物索引。" />;
  if (!data || data.available === false) return <WwDockEmpty title="研究资产索引尚未接入" note="当前会话与已有工作台仍可正常使用；这里不会用演示数据填充。" action="打开选股研究" onAction={() => onOpenStage('screen')} />;
  if (data.ok === false) return <WwDockEmpty title="研究资产读取失败" note={data.reason || '服务未返回可用结果。'} />;
  const rows = data.assets || data.items || [];
  if (!rows.length) return <WwDockEmpty title="这个空间还没有研究资产" note="从对话发起研报、选股或工作流后，真实产物会归入这里。" action="打开因子 / 工作流" onAction={() => onOpenStage('factor')} />;
  return <div style={{ padding: 15, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}><WwIndustryReviewPanel workspaceId={workspaceId} sessionId={sessionId} assets={rows} />{rows.map((a, i) => {
    const availability = window.WW.dataAvailability(a);
    const sourceAvailability = window.WW.sourceAvailability(a);
    const reportId = ['report', 'analysis'].includes(String(a.kind || ''))
      && /^wa_[0-9a-f]{16}$/.test(String(a.id || '')) ? String(a.id) : '';
    const page = a.kind === 'workflow' ? 'factor' : (a.open_page || (a.kind === 'factor' ? 'factor' : ''));
    const canOpen = page && window.WW.PAGES[page];
    const canOpenReport = !!reportId;
    const stage = a.kind === 'workflow' && a.ref
      ? { page: 'factor', load: String(a.ref) }
      : a.kind === 'factor' && (a.ref || a.id)
        ? { page: 'factor', asset: String(a.ref || a.id) }
        : { page };
    return <div key={a.id || a.path || i} onClick={() => canOpenReport ? setReportAsset({ workspaceId, id: reportId }) : canOpen && onOpenStage(stage)} style={{ padding: '10px 8px', borderBottom: '1px solid var(--line-soft)', cursor: canOpenReport || canOpen ? 'pointer' : 'default' }}>
      <div style={{ display: 'flex', gap: 8, fontFamily: 'var(--serif)', fontSize: 12.5 }}><span style={{ flex: 1 }}>{a.title || a.name || a.path || ('研究资产 ' + (i + 1))}</span>{(canOpenReport || canOpen) && <span style={{ color: 'var(--yin)', fontSize: 10.5 }}>打开 ↗</span>}</div>
      {availability && <div role="status" data-source-availability={availability.state} style={{ marginTop: 6, lineHeight: 1.6, fontSize: 10.5 }}>
        {a.dataset_id === 'diagnostic.control_lists' && <div style={{ color: 'var(--ink-2)' }}>有限连通性观测摘录 · 非研究证据。以下是观测时状态；修复后的源未重新抓取，不按预期结果标绿。</div>}
        <strong style={{ color: availability.state === 'available' ? 'var(--dai)' : 'var(--yin)' }}>{availability.label}</strong>
        <div style={{ color: 'var(--ink-2)' }}>{availability.note}</div>
        {sourceAvailability.length > 0 && <ul aria-label="名单来源可用性" style={{ margin: '5px 0', paddingLeft: 16 }}>{sourceAvailability.map((source, j) => <li key={source.id + j} data-list-source-status={source.status} style={{ color: source.status === 'ok' ? 'var(--dai)' : 'var(--yin)' }}>{source.label}：{source.labelStatus}{source.reason ? ' · ' + source.reason : ''}</li>)}</ul>}
        {(a.as_of || a.pulled_at) && <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 9 }}>观测日期 {a.as_of || '未提供'} · 抓取 {a.pulled_at || '未提供'}</div>}
      </div>}
      {a.summary && <div style={{ color: 'var(--ink-2)', fontSize: 10.5, lineHeight: 1.5, marginTop: 4 }}>{a.summary}</div>}
      <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 9.5, marginTop: 4 }}>{a.kind || a.type || 'asset'}{a.status ? ' · ' + (a.kind === 'data' && a.status === 'saved' ? '快照已保存（非数据可用结论）' : a.status) : ''}{a.updated || a.ts ? ' · ' + String(a.updated || a.ts).slice(0, 16) : ''}</div>
    </div>;
  })}</div>;
}

function WwWorkspaceFiles({ workspace, data, loading, path, onOpenPath, onOpenFile, preview, previewLoading, onCloseFile }) {
  if (previewLoading) return <WwDockEmpty title="读取代码…" note="正在从当前研究空间安全读取 UTF-8 文本。" />;
  if (preview) return <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
    <div style={{ height: 36, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 7, padding: '0 10px', borderBottom: '1px solid var(--line)' }}>
      <button onClick={onCloseFile} style={{ border: 0, background: 'transparent', color: 'var(--ink)', cursor: 'pointer' }}>←</button>
      <span title={preview.path || ''} className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 10.5 }}>{preview.path || '文件'}</span>
      {preview.sha256 && <span title={preview.sha256} className="mono" style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 8.5 }}>{preview.sha256.slice(0, 8)}</span>}
    </div>
    {preview.ok === false ? <WwDockEmpty title="文件读取失败" note={preview.reason || '文件可能已变化或不再可访问。'} />
      : <pre style={{ flex: 1, minHeight: 0, overflow: 'auto', margin: 0, padding: '13px 12px', background: 'var(--paper-2)', color: 'var(--ink-1)', fontFamily: 'var(--mono)', fontSize: 10.5, lineHeight: 1.6, whiteSpace: 'pre', tabSize: 2 }}>{preview.content || '(空文件)'}</pre>}
  </div>;
  if (loading) return <WwDockEmpty title="读取文件索引…" note="仅通过研究空间 API 读取，不会从网页越权访问本机路径。" />;
  if (!data || data.available === false) return <WwDockEmpty title="文件浏览能力尚未接入" note={(workspace && workspace.root_path ? ('已绑定工作目录：\n' + workspace.root_path + '\n') : '') + '目录内容不会被伪造；待安全文件 API 可用后在此展示。'} />;
  if (data.ok === false) return <WwDockEmpty title="文件索引读取失败" note={data.reason || '服务未返回可用结果。'} />;
  const rows = data.entries || data.files || data.items || [];
  if (!rows.length) return <WwDockEmpty title="工作目录为空" note="尚未发现可展示的文件；Agent 也不会在未授权目录外写入。" />;
  const parent = String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).slice(0, -1).join('/');
  return <div style={{ padding: '10px 12px', overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '3px 6px 9px', borderBottom: '1px solid var(--line)' }}>
      {path && <button onClick={() => onOpenPath(parent)} style={{ border: 0, background: 'transparent', color: 'var(--ink)', cursor: 'pointer', padding: 2 }}>←</button>}
      <span title={path || '/'} className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 10, color: 'var(--ink-3)' }}>{path || '/ 工作区根目录'}</span>
      {data.truncated && <span style={{ marginLeft: 'auto', color: 'var(--jin)', fontSize: 9 }}>已截断</span>}
    </div>
    {rows.map((f, i) => {
    const filePath = f.path || f.name || String(f);
    const dir = f.type === 'directory' || f.type === 'dir' || f.kind === 'dir' || f.is_dir;
    return <div key={filePath + i} title={filePath} onClick={() => dir ? onOpenPath(filePath) : onOpenFile(filePath)} style={{ padding: '7px 8px', display: 'flex', gap: 7, borderBottom: '1px solid var(--line-soft)', color: 'var(--ink-1)', fontSize: 11.5, cursor: 'pointer' }}><span style={{ color: dir ? 'var(--jin)' : 'var(--ink-3)' }}>{dir ? '▸' : '·'}</span><span className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name || filePath}</span>{!dir && f.size != null && <span className="mono" style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 9 }}>{f.size} B</span>}</div>;
  })}</div>;
}

function WwWorkspaceAgent({ workspace, data, loading, onAgentMode, modeNote }) {
  const mode = (workspace && workspace.agent_mode) || 'research';
  const archived = !!(workspace && workspace.archived);
  const modes = [['research', '只读研究'], ['collaborative', '协作写'], ['autonomous', '守护式自主']];
  const rows = (data && (data.runs || data.items)) || [];
  return (
    <div style={{ padding: 16, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 10.5, letterSpacing: 2, color: 'var(--ink-3)', marginBottom: 8 }}>代码 AGENT 权限</div>
      <div style={{ display: 'flex', border: '1px solid var(--line)', padding: 2, gap: 2 }}>
        {modes.map(m => <span key={m[0]} aria-disabled={archived ? 'true' : undefined} onClick={archived ? undefined : () => m[0] !== mode && onAgentMode(m[0])} style={{ flex: 1, textAlign: 'center', padding: '6px 2px', fontSize: 10.5, cursor: archived ? 'not-allowed' : m[0] === mode ? 'default' : 'pointer', opacity: archived && m[0] !== mode ? .55 : 1, background: m[0] === mode ? (m[0] === 'autonomous' ? 'var(--yin)' : 'var(--paper-2)') : 'transparent', color: m[0] === mode && m[0] === 'autonomous' ? 'var(--paper)' : m[0] === mode ? 'var(--ink)' : 'var(--ink-3)' }}>{m[1]}</span>)}
      </div>
      {archived && <div style={{ marginTop: 8, padding: '8px 9px', border: '1px solid var(--line)', background: 'var(--paper-2)', color: 'var(--ink-2)', fontSize: 10.5, lineHeight: 1.55 }}><span style={{ color: 'var(--yin)', fontWeight: 600 }}>只读历史</span> · 归档空间不能更改 Agent 模式；下方运行记录仍可查看。</div>}
      <div style={{ color: 'var(--ink-3)', fontSize: 10.5, lineHeight: 1.6, marginTop: 8 }}>权限按研究空间隔离。自主模式只自动放行可撤销的空间内文本修改；运行任务和发布全局资产始终需确认。不提供原始 shell、安装、提交/推送工具。项目测试代码本身不在 OS 沙箱中，可能联网或访问用户文件，确认框会再次提示风险。</div>
      {modeNote && <div role="status" style={{ marginTop: 8, color: modeNote.ok ? 'var(--dai)' : 'var(--yin)', fontSize: 10.5, lineHeight: 1.5 }}>{modeNote.text}</div>}
      <div style={{ borderTop: '1px solid var(--line)', marginTop: 18, paddingTop: 13, fontSize: 10.5, letterSpacing: 2, color: 'var(--ink-3)' }}>最近运行</div>
      {loading && <div style={{ marginTop: 12, color: 'var(--ink-3)', fontSize: 11.5 }}>读取中…</div>}
      {!loading && (!data || data.available === false) && <WwDockEmpty title="代码 Agent 运行服务尚未接入" note="权限设置会保存到研究空间；本界面不会假装已经执行代码。" />}
      {!loading && data && data.available !== false && data.ok === false && <WwDockEmpty title="代码 Agent 记录读取失败" note={data.reason || '服务未返回可用结果。'} />}
      {!loading && data && data.available !== false && data.ok !== false && !rows.length && <WwDockEmpty title="尚无代码任务" note="在对话里下达写代码任务后，真实运行记录会显示在这里。" />}
      {rows.map((r, i) => <div key={r.run_id || r.id || i} style={{ marginTop: 9, padding: '9px 8px', border: '1px solid var(--line-soft)', background: 'var(--paper-2)' }}><div style={{ display: 'flex', fontSize: 11.5 }}><span style={{ flex: 1 }}>{r.goal || r.title || r.run_id || '代码任务'}</span><span className="mono" style={{ color: r.status === 'running' ? 'var(--zhu)' : 'var(--ink-3)', fontSize: 9.5 }}>{r.status || 'unknown'}</span></div>{r.test_summary && <div style={{ color: 'var(--ink-3)', fontSize: 10, marginTop: 5 }}>{r.test_summary}</div>}</div>)}
    </div>
  );
}

function WwWorkspaceDock({ workspace, sessions, sessionId, open, narrow, onToggle, onClose, onOpenStage, onWorkspaceChanged }) {
  const [tab, setTab] = React.useState('overview');
  const [assets, setAssets] = React.useState(null);
  const [files, setFiles] = React.useState(null);
  const [filePath, setFilePath] = React.useState('');
  const [fileTargetPath, setFileTargetPath] = React.useState('');
  const [filePreview, setFilePreview] = React.useState(null);
  const [fileLoading, setFileLoading] = React.useState(false);
  const [runs, setRuns] = React.useState(null);
  const [watch, setWatch] = React.useState(null);
  const [loading, setLoading] = React.useState({});
  const [folderNote, setFolderNote] = React.useState(null);
  const [agentModeNote, setAgentModeNote] = React.useState(null);
  const [refreshToken, setRefreshToken] = React.useState(0);
  const wid = workspace && workspace.id;
  const widRef = React.useRef(wid);
  const fileRequestSeqRef = React.useRef(0);
  const watchRequestSeqRef = React.useRef(0);
  const fileTargetRef = React.useRef({ wid: null, path: '' });
  React.useEffect(() => {
    widRef.current = wid;
    fileRequestSeqRef.current += 1;
    watchRequestSeqRef.current += 1;
    fileTargetRef.current = { wid: null, path: '' };
    setAssets(null); setFiles(null); setFilePath(''); setFileTargetPath(''); setFilePreview(null); setFileLoading(false); setRuns(null); setWatch(null); setFolderNote(null); setAgentModeNote(null); setTab('overview');
  }, [wid]);
  React.useEffect(() => {
    if (!open || !wid) return;
    let dead = false;
    let first = true;
    let timer = null;
    const load = async () => {
      const key = tab === 'research' ? 'research' : tab === 'files' ? 'files' : tab === 'agent' ? 'agent' : tab === 'watch' ? 'watch' : '';
      if (!key) return;
      if (first) setLoading(x => ({ ...x, [key]: true }));
      const requestedWid = wid;
      const watchSeq = key === 'watch' ? ++watchRequestSeqRef.current : 0;
      let d;
      try {
        d = key === 'research' ? await window.WW.workspaceAssets(requestedWid)
          : key === 'files' ? await window.WW.workspaceFiles(requestedWid, filePath, 2)
          : key === 'watch' ? await window.WW.workspaceSeats(requestedWid, 50)
          : await window.WW.workspaceAgentRuns(requestedWid);
      } catch (e) {
        d = { ok: false, available: false, reason: (e && e.message) || '服务暂不可达' };
      }
      const watchIsCurrent = key !== 'watch' || (watchSeq === watchRequestSeqRef.current && widRef.current === requestedWid);
      if (!dead && watchIsCurrent) {
        if (key === 'research') setAssets(d);
        else if (key === 'files') setFiles(d);
        else if (key === 'watch') setWatch(d);
        else setRuns(d);
        setLoading(x => ({ ...x, [key]: false }));
      }
      first = false;
    };
    if (['research', 'files', 'agent', 'watch'].indexOf(tab) >= 0) {
      load();
      timer = setInterval(load, tab === 'agent' || tab === 'watch' ? 5000 : 12000);
    }
    return () => { dead = true; if (tab === 'watch') watchRequestSeqRef.current += 1; if (timer) clearInterval(timer); };
  }, [open, wid, tab, filePath, refreshToken]);
  React.useEffect(() => {
    if (!open || tab !== 'files' || !wid || !fileTargetPath) return;
    const requestedWid = wid;
    const requestedPath = fileTargetPath;
    let dead = false;
    let inFlight = false;
    const loadFile = async (first) => {
      const target = fileTargetRef.current;
      if (dead || inFlight || target.wid !== requestedWid || target.path !== requestedPath) return;
      inFlight = true;
      const seq = ++fileRequestSeqRef.current;
      if (first) setFileLoading(true);
      let d;
      try { d = await window.WW.workspaceFile(requestedWid, requestedPath); }
      catch (e) { d = { ok: false, reason: (e && e.message) || '文件读取失败', path: requestedPath }; }
      const current = fileTargetRef.current;
      if (!dead && seq === fileRequestSeqRef.current && widRef.current === requestedWid && current.wid === requestedWid && current.path === requestedPath) {
        setFilePreview(d);
        setFileLoading(false);
      }
      inFlight = false;
    };
    loadFile(true);
    const timer = setInterval(() => loadFile(false), 12000);
    return () => { dead = true; fileRequestSeqRef.current += 1; clearInterval(timer); };
  }, [open, wid, tab, fileTargetPath]);
  const invalidateFilePreview = () => {
    fileRequestSeqRef.current += 1;
    fileTargetRef.current = { wid: null, path: '' };
    setFileTargetPath('');
    setFilePreview(null);
    setFileLoading(false);
  };
  const openFilePath = (path) => { invalidateFilePreview(); setFilePath(path || ''); setFiles(null); };
  const openFile = (path) => {
    const requestedPath = String(path || '');
    fileRequestSeqRef.current += 1;
    fileTargetRef.current = { wid, path: requestedPath };
    setFilePreview(null); setFileLoading(true); setFileTargetPath(requestedPath);
  };
  const bindFolder = async () => {
    if (workspace && workspace.archived) return;
    setFolderNote(null);
    const grant = await window.WW.chooseResearchFolder();
    if (grant && grant.cancelled) return;
    if (!grant || !grant.ok || !grant.grant) { setFolderNote({ ok: false, text: (grant && grant.reason) || '没有取得目录授权' }); return; }
    const d = await window.WW.updateWorkspace(wid, { root_mode: 'external', folder_grant: grant.grant });
    if (d && d.ok && d.workspace) { onWorkspaceChanged(d.workspace); setFiles(null); setFilePath(''); invalidateFilePreview(); setRefreshToken(x => x + 1); setFolderNote({ ok: true, text: '工作文件夹已绑定' }); }
    else setFolderNote({ ok: false, text: (d && d.reason) || '工作文件夹绑定失败' });
  };
  const setAgentMode = async (mode) => {
    if (!wid || (workspace && workspace.archived)) return;
    setAgentModeNote(null);
    let d;
    try { d = await window.WW.updateWorkspace(wid, { agent_mode: mode }); }
    catch (e) { d = { ok: false, reason: (e && e.message) || 'Agent 模式更新失败' }; }
    if (d && d.ok && d.workspace) {
      if (onWorkspaceChanged) onWorkspaceChanged(d.workspace);
      setAgentModeNote({ ok: true, text: 'Agent 模式已更新' });
    } else setAgentModeNote({ ok: false, text: (d && d.reason) || 'Agent 模式更新失败' });
  };
  if (!open) return <button onClick={onToggle} title="展开研究空间与记忆中心" style={{ position: 'fixed', right: 9, top: 50, zIndex: 20, border: '1px solid var(--ink)', background: 'var(--paper)', color: 'var(--ink)', padding: '7px 8px', cursor: 'pointer', writingMode: 'vertical-rl', letterSpacing: 2, fontFamily: 'var(--serif)' }}>研究 · 记忆</button>;
  const switchTab = (nextTab) => { if (nextTab !== 'files') invalidateFilePreview(); setTab(nextTab); };
  const body = tab === 'overview' ? <WwWorkspaceOverview workspace={workspace} sessions={sessions} onOpenStage={onOpenStage} onBindFolder={bindFolder} folderNote={folderNote} />
    : tab === 'research' ? <WwWorkspaceResearch workspaceId={wid} sessionId={sessionId} data={assets} loading={loading.research} onOpenStage={onOpenStage} />
    : tab === 'files' ? <WwWorkspaceFiles workspace={workspace} data={files} loading={loading.files} path={filePath} onOpenPath={openFilePath} onOpenFile={openFile} preview={filePreview} previewLoading={fileLoading} onCloseFile={invalidateFilePreview} />
    : tab === 'agent' ? <WwWorkspaceAgent workspace={workspace} data={runs} loading={loading.agent} onAgentMode={setAgentMode} modeNote={agentModeNote} />
    : tab === 'watch' ? <WwWorkspaceWatch workspace={workspace} data={watch} loading={loading.watch} onOpenStage={onOpenStage} />
    : <WwMemoryCenter workspaceId={wid} sessionId={sessionId} />;
  return (
    <React.Fragment>
      {narrow && <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 51, background: 'rgba(28,24,20,.20)' }} />}
      <aside className={narrow ? 'ww-workspace-dock ww-workspace-dock-drawer' : 'ww-workspace-dock'} style={{ zIndex: narrow ? 52 : 2, borderLeft: '1px solid var(--line)', background: 'var(--paper)', display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
        <div style={{ height: 38, flexShrink: 0, display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--line)', padding: '0 8px' }}>
          {WW_DOCK_TABS.map(t => <span key={t[0]} onClick={() => switchTab(t[0])} style={{ padding: '11px 6px 8px', cursor: 'pointer', fontSize: 11.5, color: tab === t[0] ? 'var(--ink)' : 'var(--ink-3)', borderBottom: '2px solid ' + (tab === t[0] ? 'var(--yin)' : 'transparent'), marginBottom: -1 }}>{t[1]}</span>)}
          <span onClick={onClose} title={narrow ? '关闭' : '收起'} style={{ marginLeft: 'auto', color: 'var(--ink-3)', cursor: 'pointer', padding: 6 }}>✕</span>
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>{body}</div>
      </aside>
    </React.Fragment>
  );
}

function WwWorkspaceStage({ page, workspaceId, sessionId, load, run, workflowImport, importToken, asset, code, name, focus, readOnly, onBack }) {
  const frameRef = React.useRef(null);
  const sendWorkflow = () => {
    if (workflowImport && importToken && frameRef.current && frameRef.current.contentWindow) {
      frameRef.current.contentWindow.postMessage({ type: 'guanlan.workflow.import', token: importToken, payload: workflowImport }, location.origin);
    }
  };
  React.useEffect(() => {
    const backToChat = (event) => {
      if (event.origin !== location.origin || !event.data) return;
      if (!frameRef.current || frameRef.current.contentWindow !== event.source) return;
      if (event.data.type === 'guanlan.workflow.ready' && event.data.token === importToken) { sendWorkflow(); return; }
      if (event.data.type !== 'guanlan:ww:back-to-conversation') return;
      if (onBack) onBack();
    };
    window.addEventListener('message', backToChat);
    return () => window.removeEventListener('message', backToChat);
  }, [onBack, workflowImport, importToken]);
  const p = window.WW.PAGES[page];
  const stageWid = workspaceId || 'rw_general';
  const stageCode = page === 'seats' ? wwWatchCode(code) : '';
  const stageFocus = page === 'seats' && focus === 'watch' ? 'watch' : '';
  const tail = (run ? '&run=' + encodeURIComponent(run) : asset ? '&asset=' + encodeURIComponent(asset) : (load ? '&load=' + encodeURIComponent(load) : ''))
    + (workflowImport ? '&import=' + encodeURIComponent(importToken) : '')
    + (sessionId ? '&session_id=' + encodeURIComponent(sessionId) : '')
    + (stageCode ? '&code=' + encodeURIComponent(stageCode) : '')
    + (stageFocus ? '&focus=' + encodeURIComponent(stageFocus) : '')
    + (readOnly ? '&readonly=1' : '');
  const args = '?embed=1&ws=' + encodeURIComponent(stageWid)
    + tail;
  const handoffSeats = () => {
    if (stageCode && window.GL && window.GL.handoff) {
      window.GL.handoff('cockpit', { code: stageCode, name: name || stageCode, focus: stageFocus || 'watch', fromWorkspaceWatch: true }, stageWid);
    }
  };
  // WeiwoApp.openStage writes the initial envelope synchronously before this
  // iframe is mounted.  Do not write it again in an effect: a very fast iframe
  // could consume the first value and then receive a stale duplicate.  The
  // helper remains for the explicit external-window action below.
  if (!p) return null;
  const src = p.file + args;
  const openExternal = () => {
    handoffSeats();
    window.open(p.file + '?ws=' + encodeURIComponent(stageWid) + tail, '_blank');
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0, background: 'var(--paper)' }}>
      <div style={{ height: 38, flexShrink: 0, borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', padding: '0 13px', gap: 10 }}>
        <button onClick={onBack} style={{ border: 0, background: 'transparent', color: 'var(--ink)', cursor: 'pointer', fontFamily: 'var(--serif)' }}>← 返回对话</button>
        <span style={{ color: 'var(--line)' }}>|</span><span style={{ fontFamily: 'var(--serif)', fontWeight: 600 }}>{p.label} · 全宽研究台</span>
        {stageCode && <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 9.5 }}>{stageCode}</span>}
        <span style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 10 }}>左侧空间树已保留</span>
        <button disabled={!!workflowImport} title={workflowImport ? '未保存的图请先存入工作流库，再从历史列表打开独立窗口' : ''} onClick={openExternal} style={{ border: '1px solid var(--line)', background: 'var(--paper)', color: 'var(--ink-2)', cursor: workflowImport ? 'not-allowed' : 'pointer', padding: '4px 8px' }}>独立窗口 ↗</button>
      </div>
      <iframe ref={frameRef} src={src} onLoad={sendWorkflow} title={p.label} style={{ flex: 1, minHeight: 0, width: '100%', border: 0, background: 'var(--paper)' }} />
    </div>
  );
}

window.WwWorkspaceDock = WwWorkspaceDock;
window.WwWorkspaceStage = WwWorkspaceStage;
window.WwWorkspaceWatchNormalize = wwNormalizeWorkspaceWatch;
