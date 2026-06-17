// console-rail.jsx — 左栏:新对话 / 计划任务(agent 记忆镜像)/ 会话列表。
const WW_RAIL_H3 = { fontFamily: 'var(--serif)', fontSize: 11, fontWeight: 600, letterSpacing: 3, color: 'var(--ink-2)', margin: '16px 14px 6px', display: 'flex', alignItems: 'center', gap: 8 };

function WwRail({ state, sessions, onNew, onSwitch, onUpdate }) {
  const mark = { done: { c: 'var(--dai)', t: '✓' }, in_progress: { c: 'var(--zhu)', t: '▶' }, pending: { c: 'var(--jin)', t: '○' } };
  const [edit, setEdit] = React.useState(null);   // {sid, field:'title'|'group', value} 行内编辑态
  const commit = () => {
    if (!edit) return;
    const v = edit.value.trim();
    const orig = sessions.find(m => m.id === edit.sid) || {};
    setEdit(null);
    if (edit.field === 'title') { if (v && v !== orig.title) onUpdate(edit.sid, { title: v }); }
    else if (v !== (orig.group || '')) onUpdate(edit.sid, { group: v });
  };
  const isRun = (m) => !!m.running || (m.id === state.sid && state.busy);
  const sessRow = (m) => (edit && edit.sid === m.id) ? (
    <div key={m.id} style={{ margin: '0 10px 4px', padding: '4px 10px' }}>
      <input autoFocus value={edit.value}
        onChange={e => setEdit(ed => ({ ...ed, value: e.target.value }))}
        onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEdit(null); }}
        onBlur={commit} placeholder={edit.field === 'title' ? '会话名' : '分组名(留空 = 取消分组)'}
        style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--ink-2)', background: 'var(--paper)', padding: '5px 8px', fontSize: 12, fontFamily: 'var(--serif)', color: 'var(--ink)', outline: 'none' }} />
    </div>
  ) : (
    <div key={m.id} onClick={() => onSwitch(m.id)} className="ww-sess"
      style={{ margin: '0 10px 4px', padding: '7px 10px', fontSize: 12.5, color: 'var(--ink-1)', cursor: 'pointer', borderLeft: '2px solid ' + (m.id === state.sid ? 'var(--yin)' : 'transparent'), background: m.id === state.sid ? 'var(--paper-2)' : 'transparent', display: 'flex', alignItems: 'center', gap: 6 }}>
      <span title={isRun(m) ? '运筹中' : '空闲'} style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: isRun(m) ? 'var(--zhu)' : 'var(--line)', animation: isRun(m) ? 'pulse 1.4s infinite' : 'none' }} />
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.title}</span>
      <span className="ww-sess-act" title="改名" onClick={e => { e.stopPropagation(); setEdit({ sid: m.id, field: 'title', value: m.title || '' }); }}>✎</span>
      <span className="ww-sess-act" title="分组" onClick={e => { e.stopPropagation(); setEdit({ sid: m.id, field: 'group', value: m.group || '' }); }}>⊟</span>
      <span style={{ color: 'var(--ink-3)', fontSize: 10.5, whiteSpace: 'nowrap' }}>{(m.updated || '').slice(5, 16).replace('T', ' ')}</span>
    </div>
  );
  // 分组归并:未分组平铺在前,组按其最近会话先后排(sessions 已按 updated 降序)
  const ungrouped = sessions.filter(m => !m.group);
  const groups = [];
  sessions.forEach(m => {
    if (!m.group) return;
    let g = groups.find(x => x.name === m.group);
    if (!g) { g = { name: m.group, items: [] }; groups.push(g); }
    g.items.push(m);
  });
  return (
    <div style={{ borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--paper)' }}>
      <div onClick={onNew} style={{ margin: '12px 12px 4px', padding: '9px 0', textAlign: 'center', fontFamily: 'var(--serif)', fontSize: 13, letterSpacing: 4, border: '1.5px solid var(--ink)', cursor: 'pointer', background: 'var(--paper-2)' }}>新 对 话</div>
      <h3 style={WW_RAIL_H3}>任务计划</h3>
      {(state.plan.length === 0) && <div style={{ margin: '0 14px', fontSize: 11, color: 'var(--ink-3)' }}>暂无——下达复杂指令后 agent 会拆计划挂在这里</div>}
      {state.plan.map(t => (
        <div key={t.id} style={{ margin: '0 10px 6px', padding: '8px 10px', border: '1px solid var(--line-soft)', background: 'var(--paper-2)', fontSize: 12, opacity: t.status === 'done' ? 0.65 : 1 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 7, color: 'var(--ink-1)' }}>
            <span className="mono" style={{ color: (mark[t.status] || mark.pending).c, animation: t.status === 'in_progress' ? 'pulse 1.4s infinite' : 'none' }}>{(mark[t.status] || mark.pending).t}</span>
            <span>{t.text}</span>
          </div>
        </div>
      ))}
      <h3 style={WW_RAIL_H3}>会话</h3>
      <div style={{ overflowY: 'auto', minHeight: 0 }}>
        {ungrouped.map(sessRow)}
        {groups.map(g => (
          <React.Fragment key={g.name}>
            <div style={{ margin: '10px 14px 4px', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 2 }}>{g.name}</div>
            {g.items.map(sessRow)}
          </React.Fragment>
        ))}
      </div>
      <div style={{ marginTop: 'auto', padding: '10px 14px', borderTop: '1px solid var(--line-soft)', fontSize: 11, color: 'var(--ink-3)', display: 'flex', gap: 12 }}>
        <span>{state.connected ? '● 已连流' : '○ 重连中…'}</span>
        <span style={{ marginLeft: 'auto' }}>档案 {(window.GL && GL.stats && GL.stats().total) || 0} 件</span>
      </div>
    </div>
  );
}
window.WwRail = WwRail;
