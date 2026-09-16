// console-memory.jsx — 帷幄统一记忆中心。
// 记忆仍由后端按 sid + workspace_id 做权限和股票边界校验；前端只展示、发出显式操作。

const WW_MEMORY_SCOPE = {
  global: { label: '全局', note: '跨研究空间长期生效' },
  workspace: { label: '当前研究空间', note: '只在当前研究对象中使用' },
  session: { label: '本会话', note: '当前对话的滚动记忆' },
  archive: { label: '归档', note: '历史只读，可复制恢复' },
};

const WW_MEMORY_BUTTON = {
  border: '1px solid var(--line)', background: 'var(--paper)', color: 'var(--ink-2)',
  padding: '5px 7px', cursor: 'pointer', fontSize: 10.5, lineHeight: 1.2,
};

const WW_MEMORY_INSIGHT_SEVERITY = {
  critical: { label: '需处理', color: 'var(--yin)', order: 0 },
  warning: { label: '建议关注', color: '#8a6127', order: 1 },
  info: { label: '可优化', color: 'var(--dai)', order: 2 },
};

function wwMemoryInsightSeverity(value) {
  const raw = String(value || '').toLowerCase();
  if (raw === 'critical' || raw === 'high' || raw === 'error' || raw === 'risk') return 'critical';
  if (raw === 'warning' || raw === 'warn' || raw === 'medium' || raw === 'attention') return 'warning';
  return 'info';
}

function wwMemoryInsights(payload) {
  const empty = { summary: { status: 'healthy', score: null, total: 0, counts: { critical: 0, warning: 0, info: 0, dismissed: 0 }, telemetry: {}, generatedAt: '' }, insights: [], dismissedInsights: [] };
  if (!payload || typeof payload !== 'object') return empty;
  const activeRows = Array.isArray(payload.insights) ? payload.insights
    : Array.isArray(payload.items) ? payload.items
      : Array.isArray(payload.suggestions) ? payload.suggestions : [];
  const dismissedRows = Array.isArray(payload.dismissed_insights) ? payload.dismissed_insights
    : Array.isArray(payload.dismissed) ? payload.dismissed : [];
  const allInsights = activeRows.concat(dismissedRows.map(row => ({ ...row, dismissed: true }))).filter(row => row && typeof row === 'object').map((row, index) => {
    const impact = row.impact && typeof row.impact === 'object' ? row.impact : {};
    const memoryIds = (Array.isArray(row.memory_ids) ? row.memory_ids
      : Array.isArray(row.affected_memory_ids) ? row.affected_memory_ids
        : Array.isArray(impact.memory_ids) ? impact.memory_ids
          : Array.isArray(impact.affected_memory_ids) ? impact.affected_memory_ids : [])
      .map(String).filter(Boolean);
    const scopes = (Array.isArray(impact.scopes) ? impact.scopes : Array.isArray(row.scopes) ? row.scopes : [])
      .map(scope => wwMemoryScope(scope)).filter((scope, i, all) => all.indexOf(scope) === i);
    const rawEvidence = row.evidence == null ? [] : Array.isArray(row.evidence) ? row.evidence : [row.evidence];
    const evidence = rawEvidence.reduce((all, item) => {
      if (item == null) return all;
      if (typeof item === 'object') {
        if (item.label || item.value || item.text) all.push({ label: String(item.label || ''), value: String(item.value || item.text || '') });
        else Object.keys(item).forEach(key => all.push({ label: String(key), value: String(item[key]) }));
      } else all.push({ label: '', value: String(item) });
      return all;
    }, []);
    const actions = (Array.isArray(row.suggested_actions) ? row.suggested_actions : Array.isArray(row.actions) ? row.actions : [])
      .filter(action => action && typeof action === 'object').map(action => ({
        ...action,
        kind: String(action.kind || action.action || 'locate').toLowerCase(),
        memory_id: String(action.memory_id || action.target_memory_id || memoryIds[0] || ''),
        target_scope: String(action.target_scope || ''),
      }));
    const severity = wwMemoryInsightSeverity(row.severity || row.priority || row.level);
    return {
      ...row,
      id: String(row.id || row.insight_id || row.fingerprint || ['insight', severity, index].join('-')),
      severity,
      title: String(row.title || row.label || '记忆治理建议'),
      description: String(row.description || row.message || row.summary || row.reason || ''),
      memoryIds, scopes, evidence, actions,
      dismissed: !!row.dismissed || ['dismissed', 'resolved'].includes(String(row.status || '').toLowerCase()),
      impactDescription: String(impact.description || (typeof row.impact === 'string' ? row.impact : '')),
      impactCount: Number(impact.count || row.affected_count || memoryIds.length || 0),
    };
  }).sort((a, b) => WW_MEMORY_INSIGHT_SEVERITY[a.severity].order - WW_MEMORY_INSIGHT_SEVERITY[b.severity].order);
  const insights = allInsights.filter(row => !row.dismissed);
  const dismissedInsights = allInsights.filter(row => row.dismissed);
  const derivedCounts = insights.reduce((counts, row) => ({ ...counts, [row.severity]: counts[row.severity] + 1 }), { critical: 0, warning: 0, info: 0 });
  const rawSummary = payload.summary && typeof payload.summary === 'object' ? payload.summary
    : payload.health && typeof payload.health === 'object' ? payload.health : {};
  const telemetry = rawSummary.telemetry && typeof rawSummary.telemetry === 'object' ? rawSummary.telemetry
    : payload.telemetry && typeof payload.telemetry === 'object' ? payload.telemetry : {};
  const suppliedCounts = rawSummary.counts && typeof rawSummary.counts === 'object' ? rawSummary.counts : {};
  const hasOpenSeverityCounts = ['critical', 'warning', 'info'].some(key => suppliedCounts[key] != null);
  const suppliedSeverities = hasOpenSeverityCounts ? suppliedCounts
    : suppliedCounts.by_severity && typeof suppliedCounts.by_severity === 'object' ? suppliedCounts.by_severity : suppliedCounts;
  const counts = {
    critical: Number(suppliedSeverities.critical == null ? derivedCounts.critical : suppliedSeverities.critical),
    warning: Number(suppliedSeverities.warning == null ? derivedCounts.warning : suppliedSeverities.warning),
    info: Number(suppliedSeverities.info == null ? derivedCounts.info : suppliedSeverities.info),
    dismissed: Number(suppliedCounts.dismissed == null ? dismissedInsights.length : suppliedCounts.dismissed),
  };
  const rawStatus = String(rawSummary.status || rawSummary.health || '').toLowerCase();
  const status = rawStatus === 'risk' || rawStatus === 'critical' || counts.critical ? 'risk'
    : rawStatus === 'attention' || rawStatus === 'warning' || counts.warning ? 'attention' : 'healthy';
  const scoreValue = rawSummary.score == null ? rawSummary.health_score : rawSummary.score;
  return {
    summary: {
      ...rawSummary, status,
      score: scoreValue == null || scoreValue === '' ? null : Math.max(0, Math.min(100, Number(scoreValue))),
      total: Number(rawSummary.total == null ? insights.length : rawSummary.total),
      counts,
      telemetry,
      generatedAt: String(rawSummary.generated_at || payload.generated_at || ''),
    },
    insights,
    dismissedInsights,
  };
}

function wwMemoryScope(value, row) {
  const raw = String(value || '').toLowerCase();
  // read_only 也可能只是「当前会话/空间已归档」，不能因此抹掉真实作用层级。
  // 只有全局归档原件（scope=global_archive 或 archived=true）进入归档组。
  if (raw === 'global_archive' || raw === 'archive') return 'archive';
  if (row && row.archived && (!raw || raw.indexOf('archive') >= 0 || raw.indexOf('归档') >= 0)) return 'archive';
  if (raw === 'global' || raw === 'workspace' || raw === 'session' || raw === 'archive') return raw;
  if (raw.indexOf('archive') >= 0 || raw.indexOf('历史') >= 0 || raw.indexOf('归档') >= 0) return 'archive';
  if (raw.indexOf('workspace') >= 0 || raw.indexOf('研究空间') >= 0) return 'workspace';
  if (raw.indexOf('session') >= 0 || raw.indexOf('会话') >= 0) return 'session';
  return 'global';
}

function wwMemoryRows(payload) {
  if (!payload || typeof payload !== 'object') return [];
  let rows = Array.isArray(payload.memories) ? payload.memories
    : Array.isArray(payload.rows) ? payload.rows
    : Array.isArray(payload.items) ? payload.items : [];
  if (!rows.length && payload.groups && typeof payload.groups === 'object') {
    rows = Object.keys(WW_MEMORY_SCOPE).reduce((all, scope) => all.concat(
      Array.isArray(payload.groups[scope])
        ? payload.groups[scope].map(row => ({ ...row, scope: row.scope || scope })) : []
    ), []);
  }
  return rows.filter(row => row && typeof row === 'object').map((row, index) => {
    const scope = wwMemoryScope(row.scope || row.source_scope || row.layer, row);
    const id = String(row.id || row.memory_id || row.ref || '');
    const readOnly = scope === 'archive' || !!row.read_only;
    return {
      ...row, id, scope,
      scopeLabel: String(row.scope_label || (WW_MEMORY_SCOPE[scope] && WW_MEMORY_SCOPE[scope].label) || scope),
      text: String(row.text || row.content || row.value || ''),
      key: String(row.key || row.topic_key || ''),
      date: String(row.date || row.updated_at || row.created_at || row.ts || ''),
      pinned: !!(row.pinned || row.is_pinned),
      readOnly,
      editable: !readOnly && row.editable !== false,
      deletable: !readOnly && row.deletable !== false,
      canPin: !readOnly && row.can_pin !== false,
      canCopy: row.can_copy !== false,
      reactKey: id || [scope, row.key || '', row.date || '', index].join(':'),
    };
  });
}

async function wwMemoryRequest(path, options) {
  const response = await fetch((window.WW && window.WW.API || '') + path, {
    headers: { 'Content-Type': 'application/json', ...((options && options.headers) || {}) },
    ...(options || {}),
  });
  let data = null;
  try { data = await response.json(); } catch (e) { data = null; }
  if (!response.ok || !data || data.ok === false) {
    const error = new Error((data && (data.reason || data.message)) || ('请求失败（' + response.status + '）'));
    error.status = response.status;
    throw error;
  }
  return data;
}

function WwMemoryCopyActions({ item, busy, onCopy }) {
  if (!item.canCopy) return null;
  const targets = item.scope === 'global'
    ? [['workspace', '复制到研究空间'], ['session', '复制到本会话']]
    : item.scope === 'workspace'
      ? [['global', '复制到全局'], ['session', '复制到本会话']]
      : item.scope === 'session'
        ? [['workspace', '复制到研究空间'], ['global', '复制到全局']]
        : [['global', '恢复副本到全局']];
  return (
    <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
      {targets.map(target => <button type="button" key={target[0]} disabled={busy} onClick={() => onCopy(item, target[0])} style={{ ...WW_MEMORY_BUTTON, opacity: busy ? .5 : 1, cursor: busy ? 'wait' : 'pointer' }}>{target[1]}</button>)}
    </div>
  );
}

function wwMemoryInsightActionLabel(action) {
  if (action.label) return String(action.label);
  if (action.kind === 'pin' || action.kind === 'unpin') return action.kind === 'unpin' ? '按建议取消置顶' : '按建议置顶';
  if (action.kind === 'copy') return '按建议复制';
  if (action.kind === 'edit' || action.kind === 'update') return '定位后编辑';
  if (action.kind === 'delete' || action.kind === 'remove') return '定位并确认删除';
  return '定位后处理';
}

function WwMemoryInsightsPanel({ payload, loading, error, busyId, onRetry, onDismiss, onLocate, onAction }) {
  const [showDismissed, setShowDismissed] = React.useState(false);
  const model = wwMemoryInsights(payload);
  const telemetry = model.summary.telemetry || {};
  const warmingUp = telemetry.warming_up === true || String(telemetry.status || '').toLowerCase() === 'warming_up' || telemetry.ready === false;
  const coverage = telemetry.coverage == null ? null : Number(telemetry.coverage);
  const health = model.summary.status === 'risk'
    ? { label: '有风险', color: 'var(--yin)' }
    : model.summary.status === 'attention'
      ? { label: '需关注', color: '#8a6127' }
      : { label: '稳定', color: 'var(--dai)' };
  const score = Number.isFinite(model.summary.score) ? model.summary.score : null;
  const dismissedCount = Math.max(model.summary.counts.dismissed || 0, model.dismissedInsights.length);
  return (
    <section aria-label="记忆健康与治理建议" style={{ marginBottom: 15, border: '1px solid var(--line)', background: 'var(--paper-2)', padding: '10px 11px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
        <h3 style={{ margin: 0, fontFamily: 'var(--serif)', fontSize: 13, fontWeight: 600 }}>记忆健康</h3>
        {payload && !error && <span role="status" style={{ color: health.color, fontSize: 10 }}>{health.label}</span>}
        {score != null && <span className="mono" title="记忆健康评分" style={{ color: 'var(--ink-3)', fontSize: 9.5 }}>{score}/100</span>}
        <span style={{ flex: 1 }} />
        {loading && payload && <span style={{ color: 'var(--ink-3)', fontSize: 9 }}>正在复检…</span>}
      </div>
      {!payload && loading && <div style={{ color: 'var(--ink-3)', fontSize: 10.5, paddingTop: 8 }}>正在检查冲突、陈旧和召回质量…</div>}
      {error && <div role="status" style={{ marginTop: 8, color: 'var(--ink-3)', fontSize: 10.5, lineHeight: 1.55 }}>记忆体检暂不可用，不影响下方记忆管理。<button type="button" onClick={onRetry} style={{ ...WW_MEMORY_BUTTON, marginLeft: 7 }}>重新检查</button></div>}
      {payload && !error && <React.Fragment>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
          {Object.keys(WW_MEMORY_INSIGHT_SEVERITY).map(severity => {
            const meta = WW_MEMORY_INSIGHT_SEVERITY[severity];
            return <span key={severity} className="mono" style={{ border: '1px solid var(--line-soft)', background: 'var(--paper)', color: model.summary.counts[severity] ? meta.color : 'var(--ink-3)', padding: '3px 5px', fontSize: 9 }}>{meta.label} {model.summary.counts[severity]}</span>;
          })}
          {model.summary.generatedAt && <time className="mono" dateTime={model.summary.generatedAt} title={model.summary.generatedAt} style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 8.5, alignSelf: 'center' }}>检查于 {model.summary.generatedAt.slice(0, 16).replace('T', ' ')}</time>}
        </div>
        {warmingUp && <div role="note" style={{ marginTop: 8, borderLeft: '2px solid var(--line)', paddingLeft: 7, color: 'var(--ink-3)', fontSize: 9.5, lineHeight: 1.55 }}>正在积累使用证据，不会误判长期未使用{telemetry.observed_days != null ? ' · 已观察 ' + telemetry.observed_days + ' 天' : ''}{telemetry.total_queries != null ? ' · ' + telemetry.total_queries + ' 次召回' : ''}</div>}
        {!warmingUp && (telemetry.observed_days != null || telemetry.total_queries != null || Number.isFinite(coverage)) && <div className="mono" style={{ marginTop: 7, color: 'var(--ink-3)', fontSize: 8.5 }}>使用证据{telemetry.observed_days != null ? ' · ' + telemetry.observed_days + ' 天' : ''}{telemetry.total_queries != null ? ' · ' + telemetry.total_queries + ' 次召回' : ''}{Number.isFinite(coverage) ? ' · 覆盖 ' + (coverage <= 1 ? Math.round(coverage * 100) : Math.round(coverage)) + '%' : ''}</div>}
        {!model.insights.length && <div style={{ marginTop: 9, color: 'var(--ink-3)', fontSize: 10.5, lineHeight: 1.55 }}>暂未发现需要你处理的记忆问题。</div>}
        {!!model.insights.length && <div style={{ marginTop: 9 }}>
          <div style={{ color: 'var(--ink-3)', fontSize: 9.5, marginBottom: 6 }}>治理建议只辅助核对，不会自动改写或删除记忆。</div>
          {model.insights.map(insight => {
            const meta = WW_MEMORY_INSIGHT_SEVERITY[insight.severity];
            const insightBusy = busyId === insight.id;
            const scopeText = insight.scopes.map(scope => (WW_MEMORY_SCOPE[scope] && WW_MEMORY_SCOPE[scope].label) || scope).join('、');
            const actions = insight.actions.filter(action => action.kind !== 'locate');
            return <article key={insight.id} style={{ borderTop: '1px solid var(--line)', padding: '9px 0 2px' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 7 }}>
                <span style={{ color: meta.color, border: '1px solid currentColor', padding: '1px 4px', fontSize: 8.5, whiteSpace: 'nowrap' }}>{meta.label}</span>
                <div style={{ flex: 1, minWidth: 0 }}><strong style={{ display: 'block', color: 'var(--ink-1)', fontSize: 11, lineHeight: 1.45 }}>{insight.title}</strong>{insight.description && <div style={{ color: 'var(--ink-2)', fontSize: 10.5, lineHeight: 1.55, marginTop: 2 }}>{insight.description}</div>}</div>
              </div>
              {(insight.evidence.length || insight.impactCount || scopeText || insight.impactDescription) && <details style={{ marginTop: 6 }}><summary style={{ color: 'var(--ink-3)', cursor: 'pointer', fontSize: 9.5 }}>证据与影响范围{insight.impactCount ? ' · ' + insight.impactCount + ' 条记忆' : ''}</summary><div style={{ borderLeft: '2px solid var(--line-soft)', margin: '5px 0 0 3px', paddingLeft: 8, color: 'var(--ink-3)', fontSize: 9.5, lineHeight: 1.55 }}>{scopeText && <div>范围：{scopeText}</div>}{insight.impactDescription && <div>影响：{insight.impactDescription}</div>}{insight.evidence.map((evidence, index) => <div key={index}>{evidence.label && <span>{evidence.label}：</span>}{evidence.value}</div>)}</div></details>}
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap', marginTop: 7 }}>
                {!!insight.memoryIds.length && <button type="button" disabled={insightBusy} onClick={() => onLocate(insight.memoryIds)} style={WW_MEMORY_BUTTON}>定位{insight.memoryIds.length}条记忆</button>}
                {actions.map((action, index) => <button type="button" key={[action.kind, action.memory_id, index].join(':')} disabled={insightBusy} onClick={() => onAction(insight, action)} style={{ ...WW_MEMORY_BUTTON, color: action.kind === 'delete' || action.kind === 'remove' ? 'var(--yin)' : 'var(--ink-2)' }}>{wwMemoryInsightActionLabel(action)}</button>)}
                <span style={{ flex: 1 }} />
                <button type="button" disabled={insightBusy} onClick={() => onDismiss(insight, true)} aria-label={'忽略建议：' + insight.title} style={{ ...WW_MEMORY_BUTTON, borderColor: 'transparent', background: 'transparent', color: 'var(--ink-3)' }}>忽略建议</button>
              </div>
            </article>;
          })}
        </div>}
        {!!dismissedCount && <div style={{ marginTop: 8, paddingTop: 7, borderTop: '1px solid var(--line-soft)' }}>
          <button type="button" aria-expanded={showDismissed} onClick={() => setShowDismissed(value => !value)} style={{ ...WW_MEMORY_BUTTON, borderColor: 'transparent', background: 'transparent', color: 'var(--ink-3)', paddingLeft: 0 }}>已忽略 {dismissedCount} {showDismissed ? '收起' : '展开'}</button>
          {showDismissed && <div>{model.dismissedInsights.map(insight => <div key={insight.id} style={{ display: 'flex', alignItems: 'center', gap: 7, borderTop: '1px dotted var(--line-soft)', padding: '6px 0', color: 'var(--ink-3)', fontSize: 9.5 }}><span style={{ flex: 1 }}>{insight.title}</span><button type="button" disabled={busyId === insight.id} onClick={() => onDismiss(insight, false)} aria-label={'恢复建议：' + insight.title} style={WW_MEMORY_BUTTON}>恢复建议</button></div>)}</div>}
        </div>}
      </React.Fragment>}
    </section>
  );
}

function WwMemoryCard({ item, busy, confirmDelete, located, onEdit, onPin, onDelete, onCopy }) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(item.text);
  const [keyDraft, setKeyDraft] = React.useState(item.key);
  React.useEffect(() => { setDraft(item.text); setKeyDraft(item.key); }, [item.text, item.key]);
  const save = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    const ok = await onEdit(item, { text, key: keyDraft.trim() });
    if (ok) setEditing(false);
  };
  const pinTitle = item.pinned ? '取消置顶' : '置顶';
  return (
    <article id={item.id ? 'memory-card-' + item.id : undefined} style={{ border: '1px solid ' + (located ? 'var(--yin)' : 'var(--line-soft)'), boxShadow: located ? '0 0 0 2px rgba(153,31,31,.06)' : 'none', background: item.pinned ? 'rgba(153,31,31,.035)' : 'var(--paper-2)', padding: '10px 11px', marginBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 7 }}>
        {item.pinned && <span title="已置顶" aria-label="已置顶" style={{ color: 'var(--yin)', fontSize: 11 }}>◆</span>}
        <span style={{ color: 'var(--ink-3)', fontSize: 9, whiteSpace: 'nowrap' }}>来源 · {item.scopeLabel}</span>
        {item.resident && <span title="不会被自动收敛到归档" style={{ border: '1px solid var(--line)', color: 'var(--ink-3)', padding: '1px 4px', fontSize: 8.5, whiteSpace: 'nowrap' }}>常驻</span>}
        {item.subject_mismatch && <span title="该记忆属于此空间此前绑定的证券对象" style={{ border: '1px solid rgba(153,31,31,.25)', color: 'var(--yin)', padding: '1px 4px', fontSize: 8.5, whiteSpace: 'nowrap' }}>旧标的</span>}
        <span className="mono" title={item.key || ''} style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: item.key ? 'var(--ink-2)' : 'var(--ink-3)', fontSize: 9.5 }}>{item.key ? '(' + item.key + ')' : '无主题键'}</span>
        <time className="mono" dateTime={item.date} title={item.date} style={{ color: 'var(--ink-3)', fontSize: 9 }}>{item.date ? item.date.slice(0, 10) : '日期未知'}</time>
      </div>
      {editing ? <div>
        <input value={keyDraft} onChange={e => setKeyDraft(e.target.value)} aria-label="记忆主题键" placeholder="主题键（可选）" style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', background: 'var(--paper)', padding: '6px 7px', color: 'var(--ink)', fontSize: 10.5, marginBottom: 6 }} />
        <textarea value={draft} onChange={e => setDraft(e.target.value)} aria-label="记忆内容" rows={4} autoFocus style={{ width: '100%', boxSizing: 'border-box', resize: 'vertical', border: '1px solid var(--ink-2)', background: 'var(--paper)', padding: '7px 8px', color: 'var(--ink)', fontSize: 11.5, lineHeight: 1.65 }} />
        <div style={{ display: 'flex', gap: 6, marginTop: 7 }}>
          <button type="button" disabled={!draft.trim() || busy} onClick={save} style={{ ...WW_MEMORY_BUTTON, borderColor: 'var(--ink)', color: 'var(--ink)', opacity: !draft.trim() || busy ? .5 : 1 }}>保存</button>
          <button type="button" disabled={busy} onClick={() => { setDraft(item.text); setKeyDraft(item.key); setEditing(false); }} style={WW_MEMORY_BUTTON}>取消</button>
        </div>
      </div> : <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--ink-1)', fontSize: 11.5, lineHeight: 1.65 }}>{item.text || '（空记忆）'}</div>}
      {!editing && <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 9, flexWrap: 'wrap' }}>
        {!item.readOnly && <React.Fragment>
          {item.editable && <button type="button" disabled={busy} onClick={() => setEditing(true)} style={WW_MEMORY_BUTTON}>编辑</button>}
          {item.canPin && <button type="button" disabled={busy} onClick={() => onPin(item)} aria-label={pinTitle} style={{ ...WW_MEMORY_BUTTON, color: item.pinned ? 'var(--yin)' : 'var(--ink-2)' }}>{pinTitle}</button>}
          {item.deletable && (confirmDelete
            ? <React.Fragment><span role="status" style={{ color: 'var(--yin)', fontSize: 10.5 }}>确认删除？</span><button type="button" disabled={busy} onClick={() => onDelete(item, true)} style={{ ...WW_MEMORY_BUTTON, borderColor: 'var(--yin)', color: 'var(--yin)' }}>永久删除</button><button type="button" disabled={busy} onClick={() => onDelete(item, false)} style={WW_MEMORY_BUTTON}>保留</button></React.Fragment>
            : <button type="button" disabled={busy} onClick={() => onDelete(item)} style={WW_MEMORY_BUTTON}>删除</button>)}
        </React.Fragment>}
        {item.readOnly && <span title="归档原件不可编辑、置顶或删除" style={{ color: 'var(--ink-3)', fontSize: 10.5 }}>只读原件</span>}
        <span style={{ flex: 1 }} />
        <WwMemoryCopyActions item={item} busy={busy} onCopy={onCopy} />
      </div>}
    </article>
  );
}

function WwMemoryCenter({ workspaceId, sessionId }) {
  const [query, setQuery] = React.useState('');
  const [payload, setPayload] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [notice, setNotice] = React.useState(null);
  const [busyId, setBusyId] = React.useState('');
  const [confirmDeleteId, setConfirmDeleteId] = React.useState('');
  const [refreshToken, setRefreshToken] = React.useState(0);
  const [insightsPayload, setInsightsPayload] = React.useState(null);
  const [insightsLoading, setInsightsLoading] = React.useState(true);
  const [insightsError, setInsightsError] = React.useState('');
  const [insightBusyId, setInsightBusyId] = React.useState('');
  const [insightsRefreshToken, setInsightsRefreshToken] = React.useState(0);
  const [locatedMemoryIds, setLocatedMemoryIds] = React.useState([]);
  const requestSeqRef = React.useRef(0);
  const insightRequestSeqRef = React.useRef(0);
  const wid = String(workspaceId || 'rw_general');
  const sid = String(sessionId || '');
  const reload = () => setRefreshToken(x => x + 1);
  const reloadInsights = () => setInsightsRefreshToken(x => x + 1);
  React.useEffect(() => {
    if (!sid) { setPayload({ ok: true, memories: [] }); setLoading(false); return; }
    const seq = ++requestSeqRef.current;
    const timer = setTimeout(async () => {
      setLoading(true); setNotice(null);
      const params = new URLSearchParams({ workspace_id: wid, session_id: sid, include_archived: '1' });
      if (query.trim()) params.set('q', query.trim());
      try {
        const data = await wwMemoryRequest('/console/memories?' + params.toString());
        if (seq === requestSeqRef.current) setPayload(data);
      } catch (e) {
        if (seq === requestSeqRef.current) { setPayload(null); setNotice({ ok: false, text: e.message || '记忆读取失败' }); }
      } finally {
        if (seq === requestSeqRef.current) setLoading(false);
      }
    }, query ? 220 : 0);
    return () => { clearTimeout(timer); };
  }, [wid, sid, query, refreshToken]);
  React.useEffect(() => {
    if (!sid) { setInsightsPayload({ ok: true, summary: {}, insights: [] }); setInsightsError(''); setInsightsLoading(false); return; }
    const seq = ++insightRequestSeqRef.current;
    setInsightsLoading(true); setInsightsError('');
    const params = new URLSearchParams({ workspace_id: wid, session_id: sid, include_archived: 'true', include_dismissed: '1' });
    wwMemoryRequest('/console/memory-insights?' + params.toString()).then(data => {
      if (seq === insightRequestSeqRef.current) setInsightsPayload(data);
    }).catch(error => {
      if (seq === insightRequestSeqRef.current) setInsightsError(error.message || '记忆体检失败');
    }).finally(() => {
      if (seq === insightRequestSeqRef.current) setInsightsLoading(false);
    });
  }, [wid, sid, insightsRefreshToken]);
  React.useEffect(() => { setLocatedMemoryIds([]); }, [wid, sid]);
  React.useEffect(() => {
    if (loading || !locatedMemoryIds.length) return undefined;
    const timer = setTimeout(() => {
      const first = document.getElementById('memory-card-' + locatedMemoryIds[0]);
      if (first && first.scrollIntoView) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 50);
    return () => clearTimeout(timer);
  }, [loading, payload, locatedMemoryIds.join('|')]);
  const act = async (item, label, path, options) => {
    if (!item.id) { setNotice({ ok: false, text: '这条旧记忆没有可操作标识，请刷新后重试' }); return false; }
    setBusyId(item.id); setNotice(null);
    try {
      await wwMemoryRequest(path, options);
      setNotice({ ok: true, text: label }); reload(); reloadInsights(); return true;
    } catch (e) {
      setNotice({ ok: false, text: e.message || (label + '失败') }); return false;
    } finally { setBusyId(''); }
  };
  const mutatePath = (item, suffix) => '/console/memories/' + encodeURIComponent(item.id) + (suffix || '')
    + '?workspace_id=' + encodeURIComponent(wid) + '&session_id=' + encodeURIComponent(sid);
  const onEdit = (item, fields) => act(item, '记忆已更新', mutatePath(item), { method: 'PATCH', body: JSON.stringify({ ...fields, etag: item.etag || undefined }) });
  const onPin = item => act(item, item.pinned ? '已取消置顶' : '记忆已置顶', mutatePath(item), { method: 'PATCH', body: JSON.stringify({ pinned: !item.pinned, etag: item.etag || undefined }) });
  const onDelete = (item, confirmed) => {
    if (!confirmed) { setConfirmDeleteId(confirmed === false ? '' : item.id); return Promise.resolve(false); }
    setConfirmDeleteId('');
    return act(item, '记忆已删除', mutatePath(item), { method: 'DELETE' });
  };
  const onCopy = (item, targetScope) => act(item, targetScope === 'global' ? '已复制到全局记忆' : targetScope === 'workspace' ? '已复制到当前研究空间' : '已复制到本会话', mutatePath(item, '/copy'), {
    method: 'POST', body: JSON.stringify({ target_scope: targetScope, workspace_id: wid, session_id: sid }),
  });
  const rows = wwMemoryRows(payload);
  const locateMemories = memoryIds => {
    const ids = (Array.isArray(memoryIds) ? memoryIds : [memoryIds]).map(String).filter(Boolean).filter((id, index, all) => all.indexOf(id) === index);
    if (!ids.length) { setNotice({ ok: false, text: '这条建议没有可定位的记忆证据' }); return; }
    setQuery(''); setLocatedMemoryIds(ids);
    setNotice({ ok: true, text: '已定位 ' + ids.length + ' 条相关记忆，请核对后再处置' });
  };
  const onDismissInsight = async (insight, dismissed) => {
    if (!insight.id) { setNotice({ ok: false, text: '这条建议没有可操作标识' }); return false; }
    setInsightBusyId(insight.id); setNotice(null);
    const params = new URLSearchParams({ workspace_id: wid, session_id: sid });
    try {
      await wwMemoryRequest('/console/memory-insights/' + encodeURIComponent(insight.id) + '/dismiss?' + params.toString(), {
        method: 'POST', body: JSON.stringify({ dismissed: dismissed !== false, workspace_id: wid, session_id: sid }),
      });
      setInsightsPayload(currentPayload => {
        const current = wwMemoryInsights(currentPayload);
        const all = current.insights.concat(current.dismissedInsights).map(row => row.id === insight.id ? { ...row, dismissed: dismissed !== false, status: dismissed === false ? 'active' : 'dismissed' } : row);
        const active = all.filter(row => !row.dismissed);
        const counts = active.reduce((value, row) => ({ ...value, [row.severity]: value[row.severity] + 1 }), { critical: 0, warning: 0, info: 0 });
        counts.dismissed = all.length - active.length;
        return { ok: true, summary: { ...current.summary, status: counts.critical ? 'risk' : counts.warning ? 'attention' : 'healthy', total: active.length, counts }, insights: all };
      });
      setNotice({ ok: true, text: dismissed === false ? '建议已恢复' : '建议已忽略，可在“已忽略”中恢复' });
      // Re-fetch the authoritative health score/counts after the optimistic
      // drawer update; otherwise score can lag behind the changed issue state.
      reloadInsights();
      return true;
    } catch (error) {
      setNotice({ ok: false, text: error.message || (dismissed === false ? '恢复建议失败' : '忽略建议失败') });
      return false;
    } finally { setInsightBusyId(''); }
  };
  const onInsightAction = async (insight, action) => {
    const memoryId = String(action.memory_id || insight.memoryIds[0] || '');
    const targetIds = memoryId ? [memoryId].concat(insight.memoryIds.filter(id => id !== memoryId)) : insight.memoryIds;
    const item = rows.find(row => row.id === memoryId);
    const kind = String(action.kind || 'locate').toLowerCase();
    if (kind === 'delete' || kind === 'remove') {
      locateMemories(targetIds); if (memoryId) setConfirmDeleteId(memoryId); return false;
    }
    if (kind === 'edit' || kind === 'update' || kind === 'locate') { locateMemories(targetIds); return false; }
    if (!item) { locateMemories(targetIds); setNotice({ ok: false, text: '已定位建议证据，请在记忆卡片中手动处置' }); return false; }
    if (kind === 'pin' || kind === 'unpin') {
      if ((kind === 'pin' && item.pinned) || (kind === 'unpin' && !item.pinned)) { locateMemories(targetIds); return true; }
      setInsightBusyId(insight.id);
      try { return await onPin(item); } finally { setInsightBusyId(''); }
    }
    if (kind === 'copy') {
      const targetScope = String(action.target_scope || '');
      if (!['global', 'workspace', 'session'].includes(targetScope)) { locateMemories(targetIds); setNotice({ ok: false, text: '请在记忆卡片中明确选择复制范围' }); return false; }
      setInsightBusyId(insight.id);
      try { return await onCopy(item, targetScope); } finally { setInsightBusyId(''); }
    }
    locateMemories(targetIds); return false;
  };
  const visibleRows = query.trim() ? rows.filter(row => (row.text + ' ' + row.key).toLowerCase().indexOf(query.trim().toLowerCase()) >= 0) : rows;
  return (
    <section aria-label="记忆中心" style={{ height: '100%', overflowY: 'auto', boxSizing: 'border-box', padding: '16px 14px 30px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}><h2 style={{ margin: 0, fontFamily: 'var(--serif)', fontSize: 18, letterSpacing: 1 }}>记忆中心</h2><span className="mono" style={{ color: 'var(--ink-3)', fontSize: 9.5 }}>{rows.length} 条</span></div>
      <p style={{ margin: '6px 0 12px', color: 'var(--ink-3)', fontSize: 10.5, lineHeight: 1.55 }}>查看帷幄记住了什么，并明确管理它的作用范围。置顶或带主题键的记忆会常驻；复制不会改变原记忆。</p>
      <WwMemoryInsightsPanel payload={insightsPayload} loading={insightsLoading} error={insightsError} busyId={insightBusyId} onRetry={reloadInsights} onDismiss={onDismissInsight} onLocate={locateMemories} onAction={onInsightAction} />
      <label style={{ display: 'block', marginBottom: 13 }}><span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>搜索记忆</span><input type="search" value={query} onChange={e => { setQuery(e.target.value); setLocatedMemoryIds([]); }} placeholder="搜索内容或主题键…" style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', background: 'var(--paper-2)', color: 'var(--ink)', padding: '8px 9px', fontSize: 11.5, outline: 'none' }} /></label>
      {notice && <div role="status" aria-live="polite" style={{ marginBottom: 10, padding: '7px 8px', border: '1px solid ' + (notice.ok ? 'rgba(57,91,75,.25)' : 'rgba(153,31,31,.25)'), color: notice.ok ? 'var(--dai)' : 'var(--yin)', background: 'var(--paper-2)', fontSize: 10.5, lineHeight: 1.45 }}>{notice.text}</div>}
      {!!locatedMemoryIds.length && <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 10, color: 'var(--ink-2)', fontSize: 10 }}><span>已高亮建议关联的 {locatedMemoryIds.length} 条记忆</span><span style={{ flex: 1 }} /><button type="button" onClick={() => setLocatedMemoryIds([])} style={WW_MEMORY_BUTTON}>清除定位</button></div>}
      {loading && <div style={{ color: 'var(--ink-3)', fontSize: 11.5, padding: '20px 3px' }}>正在读取记忆…</div>}
      {!loading && !payload && <div style={{ border: '1px dashed var(--line)', padding: 18, color: 'var(--ink-3)', textAlign: 'center', fontSize: 11.5 }}>记忆服务暂不可用。对话功能不受影响，可稍后重试。<br /><button type="button" onClick={reload} style={{ ...WW_MEMORY_BUTTON, marginTop: 10 }}>重新读取</button></div>}
      {!loading && payload && !visibleRows.length && <div style={{ border: '1px dashed var(--line)', padding: 22, color: 'var(--ink-3)', textAlign: 'center', fontSize: 11.5 }}>{query ? '没有匹配的记忆' : '当前范围还没有记忆'}</div>}
      {!loading && payload && Object.keys(WW_MEMORY_SCOPE).map(scope => {
        const items = visibleRows.filter(row => row.scope === scope).sort((a, b) => Number(b.pinned) - Number(a.pinned) || String(b.date).localeCompare(String(a.date)));
        if (!items.length) return null;
        const meta = WW_MEMORY_SCOPE[scope];
        return <section key={scope} style={{ marginBottom: 17 }}><div style={{ display: 'flex', alignItems: 'baseline', gap: 7, marginBottom: 7, paddingBottom: 6, borderBottom: '1px solid var(--line)' }}><h3 style={{ margin: 0, fontFamily: 'var(--serif)', fontSize: 13, fontWeight: 600 }}>{meta.label}</h3><span className="mono" style={{ color: 'var(--ink-3)', fontSize: 9 }}>{items.length}</span><span style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 9 }}>{meta.note}</span></div>{items.map(item => <WwMemoryCard key={item.reactKey} item={item} busy={busyId === item.id} confirmDelete={confirmDeleteId === item.id} located={locatedMemoryIds.indexOf(item.id) >= 0} onEdit={onEdit} onPin={onPin} onDelete={onDelete} onCopy={onCopy} />)}</section>;
      })}
    </section>
  );
}

window.WwMemoryCenter = WwMemoryCenter;
