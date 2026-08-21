// console-rail.jsx — 左栏研究空间树:空间 → 会话。旧会话稳定归入「通用研究」。
const WW_RAIL_H3 = { fontFamily: 'var(--serif)', fontSize: 10.5, fontWeight: 600, letterSpacing: 3, color: 'var(--ink-2)', margin: '14px 14px 7px', display: 'flex', alignItems: 'center', gap: 8 };

function WwWorkspaceCreate({ onClose, onCreate, onChooseFolder }) {
  const [kind, setKind] = React.useState('stock');
  const [code, setCode] = React.useState('');
  const [displayName, setDisplayName] = React.useState('');
  const [rootMode, setRootMode] = React.useState('managed');
  const [folder, setFolder] = React.useState(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    const key = (e) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [busy]);

  const choose = async () => {
    setError('');
    const d = await onChooseFolder();
    if (d && d.cancelled) return;
    if (!d || !d.ok || !d.grant) { setError((d && d.reason) || '没有取得目录授权'); return; }
    setFolder(d);
  };
  const submit = async () => {
    const c = code.trim().toUpperCase();
    const dn = displayName.trim();
    if (!c) { setError('请填写股票或 ETF 代码'); return; }
    if (!dn) { setError('请填写研究对象名称'); return; }
    if (rootMode === 'external' && (!folder || !folder.grant)) { setError('请先选择并授权工作文件夹'); return; }
    const payload = {
      name: dn + ' · ' + c, kind, code: c, display_name: dn,
      root_mode: rootMode, agent_mode: 'collaborative',
    };
    if (rootMode === 'external') payload.folder_grant = folder.grant;
    setBusy(true); setError('');
    const d = await onCreate(payload);
    setBusy(false);
    if (d && d.ok) onClose();
    else setError((d && d.reason) || '研究空间创建失败');
  };
  const field = { width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', background: 'var(--paper)', color: 'var(--ink)', padding: '8px 10px', outline: 0, fontFamily: 'var(--serif)', fontSize: 12.5 };
  const choice = (value, label) => (
    <span onClick={() => setKind(value)} style={{ flex: 1, textAlign: 'center', padding: '7px 0', cursor: 'pointer', border: '1px solid ' + (kind === value ? 'var(--ink)' : 'var(--line)'), background: kind === value ? 'var(--paper-2)' : 'transparent', color: kind === value ? 'var(--ink)' : 'var(--ink-3)', fontSize: 12 }}>{label}</span>
  );
  return (
    <React.Fragment>
      <div onClick={() => !busy && onClose()} style={{ position: 'fixed', inset: 0, zIndex: 80, background: 'rgba(28,24,20,.24)' }} />
      <div role="dialog" aria-label="新建研究空间" style={{ position: 'fixed', zIndex: 81, left: '50%', top: '50%', transform: 'translate(-50%,-50%)', width: 420, maxWidth: 'calc(100vw - 32px)', background: 'var(--paper)', border: '1px solid var(--ink)', boxShadow: '8px 8px 0 rgba(28,24,20,.12)' }}>
        <div style={{ padding: '14px 18px 12px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center' }}>
          <div style={{ fontFamily: 'var(--serif)', fontWeight: 600, letterSpacing: 2 }}>新建研究空间</div>
          <span onClick={() => !busy && onClose()} style={{ marginLeft: 'auto', color: 'var(--ink-3)', cursor: 'pointer' }}>✕</span>
        </div>
        <div style={{ padding: 18 }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>{choice('stock', '股票')}{choice('etf', 'ETF')}</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginBottom: 5, letterSpacing: 1 }}>代码</div>
          <input autoFocus value={code} onChange={e => setCode(e.target.value)} placeholder="例如 SH600519 / SH510300" style={{ ...field, fontFamily: 'var(--mono)', marginBottom: 12 }} />
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginBottom: 5, letterSpacing: 1 }}>名称</div>
          <input value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="例如 贵州茅台 / 沪深300ETF" style={{ ...field, marginBottom: 14 }} />
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginBottom: 6, letterSpacing: 1 }}>工作目录</div>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 8, cursor: 'pointer', fontSize: 12.5 }}>
            <input type="radio" checked={rootMode === 'managed'} onChange={() => setRootMode('managed')} />
            <span><b style={{ fontWeight: 500 }}>观澜托管</b><small style={{ display: 'block', color: 'var(--ink-3)', marginTop: 2 }}>自动建立独立目录，适合新研究项目</small></span>
          </label>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer', fontSize: 12.5 }}>
            <input type="radio" checked={rootMode === 'external'} onChange={() => setRootMode('external')} />
            <span style={{ flex: 1 }}><b style={{ fontWeight: 500 }}>使用已有文件夹</b><small style={{ display: 'block', color: 'var(--ink-3)', marginTop: 2 }}>桌面端授权后，代码与研究文件留在该目录</small></span>
          </label>
          {rootMode === 'external' && <div style={{ margin: '9px 0 2px 23px', display: 'flex', alignItems: 'center', gap: 8 }}>
            <button onClick={choose} disabled={busy} style={{ border: '1px solid var(--ink-2)', background: 'var(--paper-2)', color: 'var(--ink)', padding: '6px 10px', cursor: busy ? 'default' : 'pointer', fontFamily: 'var(--serif)' }}>选择文件夹…</button>
            <span title={folder && (folder.display_path || folder.path)} style={{ minWidth: 0, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 10.5, color: folder ? 'var(--dai)' : 'var(--ink-3)' }}>{folder ? ('✓ ' + (folder.display_path || folder.path || '已授权')) : '尚未选择'}</span>
          </div>}
          {error && <div style={{ marginTop: 12, color: 'var(--yin)', fontSize: 11.5 }}>{error}</div>}
        </div>
        <div style={{ borderTop: '1px solid var(--line)', padding: '10px 18px', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button onClick={onClose} disabled={busy} style={{ border: 0, background: 'transparent', color: 'var(--ink-3)', padding: '7px 12px', cursor: 'pointer' }}>取消</button>
          <button onClick={submit} disabled={busy} style={{ border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', padding: '7px 16px', cursor: busy ? 'wait' : 'pointer', fontFamily: 'var(--serif)' }}>{busy ? '创建中…' : '创建并开始研究'}</button>
        </div>
      </div>
    </React.Fragment>
  );
}

function WwRail({ state, sessions, workspaces, workspacesStatus, activeWorkspaceId, onNew, onNewWorkspace, onChooseFolder, onSwitch, onUpdate, onArchive, onRestore }) {
  const [edit, setEdit] = React.useState(null);   // {sid,field:'title'|'workspace_id',value}
  const [closed, setClosed] = React.useState({});
  const [creating, setCreating] = React.useState(false);
  const [archiveOpen, setArchiveOpen] = React.useState(false);
  const [lifecycleSid, setLifecycleSid] = React.useState('');
  const [lifecycleError, setLifecycleError] = React.useState('');
  const generalId = (window.WW.GENERAL_WORKSPACE || {}).id || 'rw_general';
  const rows = Array.isArray(workspaces) && workspaces.length ? workspaces : [window.WW.GENERAL_WORKSPACE];
  const activeRows = rows.filter(w => !w.archived);
  const activeWorkspaceArchived = !!(rows.find(w => w.id === activeWorkspaceId) || {}).archived;
  const workspaceFor = (m) => m.workspace_id || generalId;
  const commitTitle = () => {
    if (!edit || edit.field !== 'title') return;
    const v = edit.value.trim(); const orig = sessions.find(m => m.id === edit.sid) || {};
    setEdit(null); if (v && v !== orig.title) onUpdate(edit.sid, { title: v });
  };
  const isRun = (m) => !!m.running || (m.id === state.sid && state.busy);
  const activeSessions = sessions.filter(m => !m.archived);
  const archivedSessions = sessions.filter(m => !!m.archived);
  const lifecycle = async (m, action) => {
    if (lifecycleSid || (action === 'archive' && isRun(m))) return;
    setLifecycleSid(m.id); setLifecycleError('');
    const d = await (action === 'restore' ? onRestore(m.id) : onArchive(m.id));
    setLifecycleSid('');
    if (!d || d.ok === false) setLifecycleError((d && d.reason) || (action === 'restore' ? '恢复失败' : '归档失败'));
  };
  const sessionRow = (m) => {
    if (edit && edit.sid === m.id && edit.field === 'title') return (
      <div key={m.id} style={{ margin: '0 9px 3px 21px', padding: '3px 8px' }}>
        <input autoFocus value={edit.value} onChange={e => setEdit({ ...edit, value: e.target.value })}
          onKeyDown={e => { if (e.key === 'Enter') commitTitle(); if (e.key === 'Escape') setEdit(null); }} onBlur={commitTitle}
          style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--ink-2)', background: 'var(--paper)', padding: '5px 7px', fontSize: 12, fontFamily: 'var(--serif)', color: 'var(--ink)', outline: 0 }} />
      </div>
    );
    if (edit && edit.sid === m.id && edit.field === 'workspace_id') return (
      <div key={m.id} style={{ margin: '0 9px 5px 21px', padding: '4px 7px', background: 'var(--paper-2)', borderLeft: '2px solid var(--yin)' }}>
        <div style={{ fontSize: 10, color: 'var(--ink-3)', marginBottom: 4 }}>移动“{m.title}”到</div>
        <select autoFocus value={edit.value} onChange={e => { const wid = e.target.value; setEdit(null); if (wid !== workspaceFor(m)) onUpdate(m.id, { workspace_id: wid }); }} onBlur={() => setEdit(null)}
          style={{ width: '100%', border: '1px solid var(--line)', background: 'var(--paper)', padding: '5px 7px', color: 'var(--ink)', fontSize: 11.5 }}>
          {activeRows.map(w => <option key={w.id} value={w.id}>{w.name || w.display_name || w.id}</option>)}
        </select>
      </div>
    );
    return (
      <div key={m.id} onClick={() => onSwitch(m.id)} className="ww-sess"
        style={{ margin: '0 9px 3px 21px', padding: '6px 8px', fontSize: 12, color: 'var(--ink-1)', cursor: 'pointer', borderLeft: '2px solid ' + (m.id === state.sid ? 'var(--yin)' : 'transparent'), background: m.id === state.sid ? 'var(--paper-2)' : 'transparent', display: 'flex', alignItems: 'center', gap: 6 }}>
        <span title={isRun(m) ? '运筹中' : '空闲'} style={{ width: 5, height: 5, borderRadius: '50%', flexShrink: 0, background: isRun(m) ? 'var(--zhu)' : 'var(--line)', animation: isRun(m) ? 'pulse 1.4s infinite' : 'none' }} />
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.title}</span>
        <span className="ww-sess-act" title="改名" onClick={e => { e.stopPropagation(); setEdit({ sid: m.id, field: 'title', value: m.title || '' }); }}>✎</span>
        <span className="ww-sess-act" title="移动到其他研究空间" onClick={e => { e.stopPropagation(); setEdit({ sid: m.id, field: 'workspace_id', value: workspaceFor(m) }); }}>↪</span>
        <span className="ww-sess-act" title={isRun(m) ? '任务运行中，暂不能归档' : '归档对话'} aria-disabled={isRun(m) ? 'true' : undefined}
          onClick={e => { e.stopPropagation(); lifecycle(m, 'archive'); }} style={{ opacity: isRun(m) ? .25 : undefined }}>{lifecycleSid === m.id ? '…' : '归档'}</span>
        <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 9, whiteSpace: 'nowrap' }}>{(m.updated || '').slice(5, 10)}</span>
      </div>
    );
  };
  const badge = (w) => w.kind === 'stock' ? '股' : w.kind === 'etf' ? 'ETF' : '研';
  return (
    <div style={{ borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, background: 'var(--paper)' }}>
      <div style={{ display: 'flex', gap: 6, margin: '11px 10px 5px' }}>
        <div onClick={() => !activeWorkspaceArchived && onNew(activeWorkspaceId || generalId)} title={activeWorkspaceArchived ? '已归档空间只保留历史，不可新建对话' : '在当前研究空间新建对话'} style={{ flex: 1, padding: '8px 0', textAlign: 'center', fontFamily: 'var(--serif)', fontSize: 12, letterSpacing: 2, border: '1.5px solid var(--ink)', cursor: activeWorkspaceArchived ? 'not-allowed' : 'pointer', opacity: activeWorkspaceArchived ? .45 : 1, background: 'var(--paper-2)' }}>＋ 新对话</div>
        <div onClick={() => setCreating(true)} title="为一只股票或 ETF 建立独立研究空间" style={{ width: 34, display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px solid var(--line)', cursor: 'pointer', color: 'var(--yin)', fontSize: 17 }}>＋</div>
      </div>
      <h3 style={WW_RAIL_H3}><span>研究空间</span><span className="mono" style={{ marginLeft: 'auto', letterSpacing: 0, fontSize: 9, color: 'var(--ink-3)' }}>{rows.length}</span></h3>
      {workspacesStatus && !workspacesStatus.ok && <div style={{ margin: '0 10px 8px', padding: '6px 8px', background: 'rgba(168,57,45,.05)', color: 'var(--yin)', fontSize: 10.5, lineHeight: 1.45 }}>{workspacesStatus.reason || '兼容模式：研究空间服务暂不可用'}</div>}
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {rows.map(w => {
          const items = activeSessions.filter(m => workspaceFor(m) === w.id);
          const active = activeWorkspaceId === w.id;
          const shut = !!closed[w.id];
          const title = w.name || w.display_name || w.code || w.id;
          return (
            <div key={w.id} style={{ marginBottom: 3 }}>
              <div onClick={() => setClosed(c => ({ ...c, [w.id]: !c[w.id] }))} className="hover-row" style={{ display: 'flex', alignItems: 'center', gap: 7, margin: '1px 8px', padding: '8px 7px', cursor: 'pointer', borderLeft: '3px solid ' + (active ? 'var(--ink)' : 'transparent') }}>
                <span style={{ width: 25, height: 19, border: '1px solid ' + (active ? 'var(--yin)' : 'var(--line)'), color: active ? 'var(--yin)' : 'var(--ink-3)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: w.kind === 'etf' ? 8 : 10, flexShrink: 0 }}>{badge(w)}</span>
                <span style={{ minWidth: 0, flex: 1 }}>
                  <span title={title} style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'var(--serif)', fontSize: 12.5, color: w.archived ? 'var(--ink-3)' : active ? 'var(--ink)' : 'var(--ink-1)', fontWeight: active ? 600 : 400 }}>{title}{w.archived ? ' · 已归档' : ''}</span>
                  <span className="mono" style={{ display: 'block', fontSize: 8.8, color: 'var(--ink-3)', marginTop: 2 }}>{w.archived ? '只读历史' : (w.code || (w.root_path ? '已绑定目录' : '通用'))} · {items.length} 个对话</span>
                </span>
                <span title={shut ? '展开' : '收起'} style={{ color: 'var(--ink-3)', fontSize: 9 }}>{shut ? '▸' : '▾'}</span>
                {!w.archived && <span title="在此空间新建对话" onClick={e => { e.stopPropagation(); onNew(w.id); }} style={{ color: 'var(--yin)', fontSize: 15, cursor: 'pointer' }}>＋</span>}
              </div>
              {!shut && <div>
                {items.map(sessionRow)}
                {!items.length && <div style={{ margin: '2px 16px 7px 33px', color: 'var(--ink-3)', fontSize: 10.5 }}>{w.archived ? '暂无历史对话' : '暂无对话 · 点右侧 ＋ 开始'}</div>}
              </div>}
            </div>
          );
        })}
        <div style={{ margin: '10px 8px 12px', borderTop: '1px solid var(--line-soft)', paddingTop: 8 }}>
          <div onClick={() => setArchiveOpen(v => !v)} className="hover-row" style={{ display: 'flex', alignItems: 'center', padding: '7px 8px', cursor: 'pointer', color: 'var(--ink-2)', fontSize: 11.5 }}>
            <span style={{ fontFamily: 'var(--serif)', letterSpacing: 1 }}>已归档对话</span>
            <span className="mono" style={{ marginLeft: 7, color: 'var(--ink-3)', fontSize: 9 }}>{archivedSessions.length}</span>
            <span style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 9 }}>{archiveOpen ? '▾' : '▸'}</span>
          </div>
          {lifecycleError && <div role="status" style={{ margin: '3px 8px 6px', color: 'var(--yin)', fontSize: 10.5, lineHeight: 1.45 }}>{lifecycleError}</div>}
          {archiveOpen && <div>
            {!archivedSessions.length && <div style={{ padding: '7px 8px', color: 'var(--ink-3)', fontSize: 10.5 }}>暂无归档对话</div>}
            {archivedSessions.map(m => {
              const w = rows.find(x => x.id === workspaceFor(m));
              return <div key={m.id} onClick={() => onSwitch(m.id)} className="ww-sess hover-row" style={{ margin: '1px 2px', padding: '7px 8px', display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer', borderLeft: '2px solid ' + (m.id === state.sid ? 'var(--yin)' : 'transparent') }}>
                <span style={{ color: 'var(--ink-3)', fontSize: 10 }}>▧</span>
                <span style={{ minWidth: 0, flex: 1 }}>
                  <span title={m.title} style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--ink-2)', fontSize: 11.5 }}>{m.title}</span>
                  <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--ink-3)', fontSize: 9 }}>{(w && (w.display_name || w.name)) || '通用研究'} · {(m.archived_at || m.updated || '').slice(0, 10)}</span>
                </span>
                <span className="ww-sess-act" title="恢复到原研究空间" onClick={e => { e.stopPropagation(); lifecycle(m, 'restore'); }}>{lifecycleSid === m.id ? '…' : '恢复'}</span>
              </div>;
            })}
          </div>}
        </div>
      </div>
      <div style={{ marginTop: 'auto', padding: '9px 12px', borderTop: '1px solid var(--line-soft)', fontSize: 10.5, color: 'var(--ink-3)', display: 'flex', gap: 9 }}>
        <span>{state.connected ? '● 已连流' : '○ 重连中…'}</span>
        <span style={{ marginLeft: 'auto' }}>档案 {(window.GL && GL.stats && GL.stats().total) || 0} 件</span>
      </div>
      {creating && <WwWorkspaceCreate onClose={() => setCreating(false)} onCreate={onNewWorkspace} onChooseFolder={onChooseFolder} />}
    </div>
  );
}
window.WwRail = WwRail;
