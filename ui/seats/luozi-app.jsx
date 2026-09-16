// 观澜 · 落子 — 主壳(2026-07-11 三页重排:今日 | 复盘 | 策略)
//   三轴(盯盘·校场 × 复盘·实盘 × 单标·舰队)退役 → 单轴 page;逐bar推演/Deliberation/播放条/
//   影子组合/台账记账/前端盯盘循环 全部退役(盯盘归后端 watcher,留痕归 DecisionTrail)。
//   复盘真跑改「向导驱动」:票/策略/日期区间/粒度 → 一键跑,与游标彻底解耦(拔掉两个静默失败门)。
const { useMemo, useCallback: useCB } = React;

// 帷幄融合旗(对齐 screen-app):EMBED=被帷幄嵌入右栏(只隐顶栏身份区);WS=帷幄会话 id,handoff 信箱按会话取防串扰。
const WW_EMBED = new URLSearchParams(location.search).get('embed') === '1';
const WW_WS = new URLSearchParams(location.search).get('ws') || '';

// /seats/decisions 的单次回读上限也是 300。真跑不超过这个上限，才能在注册 run 头前
// 用服务端落盘事实完整核对一次，避免「在途请求已落盘、前端停止后却没计入汇总」。
const LZ_RUN_RECONCILE_LIMIT = 300;

function lzSummarizeRunRows(rows) {
  const ordered = (Array.isArray(rows) ? rows : []).filter(Boolean).slice().sort((a, b) =>
    String(a.asof || a.ts || '').localeCompare(String(b.asof || b.ts || '')));
  let nBuy = 0, nSell = 0, nWatch = 0;
  ordered.forEach(r => {
    const direction = String(r.direction || '');
    if (/买/.test(direction)) nBuy++;
    else if (/卖/.test(direction)) nSell++;
    else nWatch++;
  });
  const first = ordered[0] || null, last = ordered[ordered.length - 1] || null;
  return {
    total: ordered.length, nBuy, nSell, nWatch,
    firstDate: first ? String(first.asof || first.ts || '') : '',
    lastDate: last ? String(last.asof || last.ts || '') : '',
    lastModel: last ? String(last.model_name || '') : '',
  };
}

async function lzReadRunRowsStrict(runId, code, strategyId) {
  const api = window.GUANLAN_BACKEND || '';
  const codeCore = String(code || '').replace(/\D/g, '');
  const wantStrategy = String(strategyId || '').trim();
  if (!api || !runId || !codeCore || !wantStrategy) throw new Error('落子后端未连接或 run 身份不完整');
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(api + '/seats/decisions?run_id=' + encodeURIComponent(runId)
        + '&limit=' + LZ_RUN_RECONCILE_LIMIT);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const j = await r.json();
      if (!j || !j.ok || !Array.isArray(j.decisions)) throw new Error('落盘回读格式异常');
      return j.decisions.filter(row => row
        && row.kind === 'decide'
        && String(row.run_id || '') === String(runId)
        && String(row.code || '').replace(/\D/g, '') === codeCore
        && String(row.strategy_id || '').trim() === wantStrategy);
    } catch (e) {
      lastError = e;
      if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 180 * (attempt + 1)));
    }
  }
  throw lastError || new Error('落盘回读失败');
}

// ② 五档盘口 + 逐笔成交面板(纯展示,数据来自 /seats/orderbook + /seats/ticks;tdx 经统一 live_client)。
//   null-safe:book/ticks 缺失或 ok:false → 显 note 降级,绝不塞 0 价假档。红线:只读展示,绝不喂研判/信号。
function OrderbookTicksPanel({ book, ticks }) {
  const asks = (book && book.ok ? (book.levels || []) : []).slice().reverse();   // 卖5..卖1 由上到下
  const bids = (book && book.ok) ? (book.levels || []) : [];
  const lc = book && book.last_close;
  const px = (p) => (p == null ? '—' : (+p).toFixed(2));
  const col = (p) => ((lc != null && p != null) ? (+p > +lc ? 'var(--zhu,#c0392b)' : (+p < +lc ? 'var(--fall,#1f9d55)' : 'var(--ink-2)')) : 'var(--ink-2)');
  const lvlRow = (lv, side) => {
    const p = side === '卖' ? lv.ask : lv.bid, vol = side === '卖' ? lv.ask_vol : lv.bid_vol;
    return (
      <div key={side + lv.level} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, fontFamily: 'var(--font-mono)', padding: '1px 6px' }}>
        <span style={{ color: 'var(--ink-3)', width: 30 }}>{side}{lv.level}</span>
        <span style={{ color: col(p) }}>{px(p)}</span>
        <span style={{ color: 'var(--ink-2)', width: 64, textAlign: 'right' }}>{vol == null ? '—' : vol}</span>
      </div>
    );
  };
  return (
    <div style={{ flexShrink: 0, borderTop: '1px solid var(--line)', padding: '6px 4px' }}>
      <div style={{ fontSize: 11, color: 'var(--ink-2)', fontWeight: 600, marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
        <span>五档盘口 · 逐笔</span><span style={{ fontSize: 9, color: 'var(--ink-4)' }} title="tdx 经统一 live_client 现拉">tdx 实时</span>
      </div>
      {(!book || !book.ok) ? (
        <div style={{ fontSize: 10, color: 'var(--ink-4)', padding: '4px 6px' }}>{(book && book.note) || '盘口加载中 / tdx 不可达(非交易时段无挂单)'}</div>
      ) : (
        <div>{asks.map(l => lvlRow(l, '卖'))}<div style={{ borderTop: '1px dashed var(--line)', margin: '2px 0' }} />{bids.map(l => lvlRow(l, '买'))}</div>
      )}
      <div style={{ fontSize: 9, color: 'var(--ink-4)', margin: '5px 6px 2px' }}>逐笔成交</div>
      {(!ticks || !ticks.ok || !(ticks.ticks || []).length) ? (
        <div style={{ fontSize: 10, color: 'var(--ink-4)', padding: '0 6px 4px' }}>{(ticks && ticks.note) || '无逐笔(非交易时段/tdx 不可达)'}</div>
      ) : (
        <div style={{ maxHeight: 96, overflowY: 'auto' }}>
          {(ticks.ticks || []).slice(0, 20).map((t, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--font-mono)', padding: '0 6px' }}>
              <span style={{ color: 'var(--ink-4)', width: 64 }}>{t.time || '—'}</span>
              <span style={{ color: t.side === 'buy' ? 'var(--zhu,#c0392b)' : (t.side === 'sell' ? 'var(--fall,#1f9d55)' : 'var(--ink-3)') }}>{t.price == null ? '—' : (+t.price).toFixed(2)}</span>
              <span style={{ color: 'var(--ink-3)', width: 52, textAlign: 'right' }}>{t.vol == null ? '—' : t.vol}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const APP_TF_CN = { day: '日线', '30min': '30分钟', '5min': '5分钟', '1min': '1分钟' };
const APP_CADENCE_CN = { daily: '每日一次', '60min': '每60分钟', hourly: '每60分钟', '30min': '每30分钟', '10min': '每10分钟' };

// Pro·10席事件深研授权是一个独立、显式的用户动作：读状态可自动，签发/撤销绝不自动。
// 响应兼容 authorization/deep_authorization/lease 包装；字段缺失就显“—”，不猜授权有效。
function appDeepAuthMeta(payload) {
  const envelope = (payload && typeof payload === 'object') ? payload : {};
  const raw = (envelope.authorization && typeof envelope.authorization === 'object') ? envelope.authorization
    : ((envelope.deep_authorization && typeof envelope.deep_authorization === 'object') ? envelope.deep_authorization
      : ((envelope.lease && typeof envelope.lease === 'object') ? envelope.lease : envelope));
  const status = String(raw.status || envelope.status || '').trim().toLowerCase();
  const explicitActive = (typeof raw.authorized === 'boolean') ? raw.authorized
    : ((typeof raw.active === 'boolean') ? raw.active : null);
  const active = explicitActive != null ? explicitActive
    : ['active', 'not_yet_valid', 'stale', 'authorized', 'valid', 'issued', 'granted', 'pending'].indexOf(status) >= 0;
  const num = (...xs) => {
    const hit = xs.find(x => x != null && x !== '' && isFinite(+x));
    return hit == null ? null : Math.max(0, +hit);
  };
  const maxRuns = num(raw.max_runs, raw.max_admissions);
  const usedRuns = num(raw.runs_used, raw.admissions_used);
  const runsRemaining = num(raw.runs_remaining, raw.remaining_runs, raw.admissions_remaining,
    (maxRuns != null && usedRuns != null) ? maxRuns - usedRuns : null);
  const maxLLM = num(raw.max_llm_invocations, raw.budget_cap_llm_invocations);
  const usedLLM = num(raw.llm_invocations_used, raw.budget_used, raw.used_llm_invocations);
  const llmRemaining = num(raw.llm_invocations_remaining, raw.remaining_llm_invocations,
    (maxLLM != null && usedLLM != null) ? maxLLM - usedLLM : null);
  const bool = (...xs) => {
    const hit = xs.find(x => typeof x === 'boolean');
    return typeof hit === 'boolean' ? hit : null;
  };
  const period = String(raw.period || raw.effective_evidence_tf || raw.evidence_tf || raw.requested_evidence_tf
    || envelope.period || envelope.effective_evidence_tf || envelope.evidence_tf || envelope.requested_evidence_tf || '').trim();
  const periodsRaw = raw.supported_periods || raw.eligible_periods || envelope.supported_periods || envelope.eligible_periods;
  const supportedPeriods = Array.isArray(periodsRaw) ? periodsRaw.map(x => String(x)).filter(Boolean)
    : (typeof periodsRaw === 'string' && periodsRaw.trim() ? periodsRaw.split(',').map(x => x.trim()).filter(Boolean) : []);
  const globalRaw = raw.global_deep_budget != null ? raw.global_deep_budget : envelope.global_deep_budget;
  const globalObj = globalRaw && typeof globalRaw === 'object' ? globalRaw : {};
  const globalRemaining = num(globalObj.remaining, globalObj.runs_remaining, globalObj.remaining_runs,
    globalObj.available, globalObj.available_runs, (typeof globalRaw === 'number' ? globalRaw : null));
  const globalLLMRemaining = num(globalObj.llm_invocations_remaining, globalObj.remaining_llm_invocations);
  const globalBudgetText = (typeof globalRaw === 'string') ? globalRaw.trim() : '';
  return {
    supported: true,
    active,
    status: status || (active ? 'active' : 'none'),
    authorizationId: String(raw.authorization_id || raw.lease_id || raw.id || '').trim(),
    revision: num(raw.assignment_revision, envelope.assignment_revision, raw.revision, envelope.revision),
    runsRemaining,
    llmRemaining,
    validUntil: raw.valid_until || raw.expires_at || envelope.valid_until || envelope.expires_at || null,
    requiredConfirmText: String(raw.required_confirm_text || envelope.required_confirm_text || '').trim(),
    eligible: bool(raw.eligible, envelope.eligible),
    periodSupported: bool(raw.period_supported, raw.supports_period, envelope.period_supported, envelope.supports_period),
    period,
    supportedPeriods,
    sessionLabel: String(raw.session_label || envelope.session_label || '').trim(),
    globalRemaining,
    globalLLMRemaining,
    globalBudgetText,
    detail: String(envelope.detail || raw.detail || '').trim(),
    reason: String(envelope.eligibility_reason || envelope.period_reason || envelope.reason
      || raw.eligibility_reason || raw.period_reason || raw.terminal_reason || raw.reason || '').trim(),
  };
}

const APP_DEEP_REASON_CN = {
  assignment_not_found: '尚未保存本票盯盘配置',
  assignment_disabled: '本票自动研判尚未启用',
  assignment_not_runnable: '本票盯盘配置当前不可运行',
  thinking_mode_not_deep: '已保存配置不是 Pro·10席事件深研',
  unsupported_evidence_tf_for_10_agent: '当前数据周期尚不支持10席深研（首版仅日线）',
  deep_preset_unavailable: '10席深研编排预设当前不可用',
  authorization_preset_mismatch_requires_revoke: '编排版本已更新，请先撤销旧授权再重新授权',
  revision_conflict: '盯盘配置已更新，请重新读取后再授权',
  session_changed: '目标交易时段已变化，请重新读取后再授权',
  confirm_text_mismatch: '确认文字不一致，未授权',
  authorization_conflict: '本交易时段已有一份不同授权，请先撤销',
  assignment_session_already_consumed: '本票本交易时段已完成或已消耗一次深研，不能重复授权',
  authorization_not_found: '未找到可撤销的授权',
  invalid_authorization_request: '授权请求不符合服务端约束',
  deep_authorization_store_error: '授权记录暂不可用',
  deep_authorization_status_unavailable: '授权状态暂不可用',
  deep_authorization_issue_failed: '授权签发失败',
  revoked: '授权已撤销',
  expired: '授权已过期',
  runs_exhausted: '本交易时段深研次数已用尽',
  llm_exhausted: '本交易时段模型调用额度已用尽',
  preset_mismatch: '授权绑定的编排版本已失效',
};

function appDeepAuthReasonCN(reason) {
  const raw = String(reason || '').trim();
  return APP_DEEP_REASON_CN[raw] || raw;
}

function appDeepAuthError(payload, fallback) {
  if (!payload || typeof payload !== 'object') return fallback;
  const value = payload.reason != null ? payload.reason
    : (payload.error != null ? payload.error : (payload.detail != null ? payload.detail : payload.message));
  if (value == null || value === '') return fallback;
  return typeof value === 'string' ? value : JSON.stringify(value);
}

async function appDeepAuthRequest(code, action, current, confirmText) {
  const API = (window.GUANLAN_BACKEND || '');
  const c = String(code || '').replace(/^(SH|SZ|BJ)/i, '');
  if (!API || !c) return { ok: false, supported: null, reason: '未连接后端或股票代码为空' };
  const base = API + '/seats/watch/assignments/' + encodeURIComponent(c) + '/deep-authorization';
  const authId = current && current.authorizationId;
  const isRead = action === 'read';
  if (action === 'revoke' && !authId) return { ok: false, supported: true, reason: '后端未返回授权 ID，无法安全撤销' };
  if (action === 'authorize' && (!current || current.revision == null)) return { ok: false, supported: true, reason: '后端未返回授权版本，无法安全签发' };
  if (action === 'authorize' && !String(current.requiredConfirmText || '').trim()) return { ok: false, supported: true, reason: '后端未返回必需确认文案，未发起授权' };
  if (action === 'authorize' && String(confirmText == null ? '' : confirmText) !== current.requiredConfirmText) return { ok: false, supported: true, reason: 'confirm_text_mismatch' };
  const url = action === 'revoke'
    ? (API + '/seats/watch/deep-authorizations/' + encodeURIComponent(authId) + '/revoke')
    : base;
  const options = isRead ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(action === 'revoke'
      ? { reason: '用户撤销' }
      : {
          expected_revision: (current && current.revision != null) ? current.revision : 0,
          max_runs: 1,
          max_llm_invocations: 20,
          reason: '当日盯盘10席深研',
          confirm_text: String(confirmText == null ? '' : confirmText),
        }),
  };
  try {
    const res = await fetch(url, options);
    if (res.status === 404 || res.status === 405) return { ok: false, supported: false, reason: 'Pro·10席授权接口未接通' };
    const j = await res.json().catch(() => null);
    if (!res.ok || !j || j.ok === false) {
      return Object.assign({}, appDeepAuthMeta(j), { ok: false, supported: true, reason: appDeepAuthError(j, '授权接口 HTTP ' + res.status) });
    }
    return Object.assign({ ok: true }, appDeepAuthMeta(j));
  } catch (e) {
    return { ok: false, supported: null, reason: 'Pro·10席授权接口不可达' };
  }
}

// 全局总闸只放顶栏；详细状态按需展开，不再与单票配置、条件触发监控混成一条横栏。
function WatchControl({ watch, onToggle, assignmentMode }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const connected = watch !== null;
  const on = !!(watch && watch.enabled);
  const toggle = async (e) => {
    e.stopPropagation();
    if (!connected || busy || !onToggle) return;
    setBusy(true); try { await onToggle(!on); } finally { setBusy(false); }
  };
  return (
    <div style={{ position: 'relative', flexShrink: 0 }}>
      <div className="mono" onClick={() => setOpen(v => !v)} title="自动盯盘总开关与运行详情"
        style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', padding: '4px 8px', borderRadius: 8, border: '1px solid ' + (on ? 'var(--zhu-soft)' : 'var(--line)'), background: on ? 'rgba(168,57,45,0.055)' : 'transparent', whiteSpace: 'nowrap' }}>
        <span onClick={toggle} style={{ color: on ? 'var(--yin)' : 'var(--ink-3)', fontSize: 9.5, fontWeight: on ? 600 : 400 }}>
          {busy ? '◌ 切换中' : (connected ? (on ? '● 自动盯盘' : '○ 自动盯盘') : '○ 自动盯盘未连接')}
        </span>
        {connected && <span style={{ color: 'var(--ink-3)', fontSize: 8.5 }}>{(watch.watching || []).length}票</span>}
        <span style={{ color: 'var(--ink-3)', fontSize: 8 }}>{open ? '▴' : '▾'}</span>
      </div>
      {open && (
        <div onClick={e => e.stopPropagation()} style={{ position: 'absolute', top: 34, right: 0, width: 300, zIndex: 80, background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 10, boxShadow: '0 8px 28px rgba(28,24,20,0.18)', padding: '11px 13px' }}>
          <div className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-1)' }}>自动盯盘 · 全局总闸</div>
          {!connected ? (
            <div className="mono" style={{ marginTop: 8, fontSize: 9.5, lineHeight: 1.6, color: 'var(--ink-3)' }}>状态端点不可达。手动研判与条件触发监控不受此总闸控制。</div>
          ) : (
            <div className="mono" style={{ marginTop: 8, display: 'grid', gridTemplateColumns: '80px 1fr', rowGap: 6, fontSize: 9.5, color: 'var(--ink-3)' }}>
              <span>运行状态</span><b style={{ color: on ? 'var(--yin)' : 'var(--ink-2)' }}>{on ? (watch.marketOpen ? '盘中运行' : '已开启 · 下次开盘恢复') : '已关闭'}</b>
              <span>纳入标的</span><span>{(watch.watching || []).length} 票</span>
              <span>今日调用</span><span>{watch.todayCount != null ? watch.todayCount : '—'}{watch.budget != null ? ' / ' + watch.budget : ''}</span>
              <span>上次巡检</span><span>{watch.lastTick ? String(watch.lastTick).replace('T', ' ').slice(5, 16) : '—'}</span>
              <span>单票配置</span><span>{assignmentMode && assignmentMode.supported === true ? 'assignments 已接通' : (assignmentMode && assignmentMode.legacy ? '旧版绑定(分钟口径不可用)' : '配置接口不可达')}</span>
            </div>
          )}
          <div className="serif" style={{ borderTop: '1px solid var(--line-soft)', marginTop: 9, paddingTop: 8, fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.6 }}>总闸只控制服务端自动研判。单票纳入和周期在左栏配置；条件单是本页触发监控。</div>
        </div>
      )}
    </div>
  );
}

function WatchAssignmentDialog({ meta, assignment, assignmentMode, strategies, legacyEnabled, onClose, onSave }) {
  const legacy = !!(assignmentMode && assignmentMode.legacy);
  const unavailable = !legacy && !(assignmentMode && assignmentMode.supported === true);
  const explicit = (strategies || []).find(s => Array.isArray(s.bind) && s.bind.map(x => String(x).replace(/^(SH|SZ|BJ)/i, '')).indexOf(String(meta.code)) >= 0);
  const initialSid = (assignment && assignment.strategy_id) || (explicit && explicit.id) || '__dedicated__';
  const [enabled, setEnabled] = useState(assignment ? !!assignment.enabled : !!legacyEnabled);
  const [strategyId, setStrategyId] = useState(initialSid);
  const [evidenceTF, setEvidenceTF] = useState((assignment && (assignment.requested_evidence_tf || assignment.evidence_tf)) || 'day');
  const [cadence, setCadence] = useState((assignment && assignment.decision_cadence) || '60min');
  const [thinking, setThinking] = useState((assignment && assignment.thinking_mode) === 'deep' ? 'deep' : 'fast');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [deepAuth, setDeepAuth] = useState(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);
  const [authNote, setAuthNote] = useState('');
  const persistedPeriod = String((assignment && (assignment.requested_evidence_tf || assignment.evidence_tf)) || '');
  const periodDirty = !!(persistedPeriod && String(evidenceTF) !== persistedPeriod);
  const draftDirty = !assignment
    || enabled !== !!assignment.enabled
    || String(strategyId) !== String(assignment.strategy_id || '')
    || String(evidenceTF) !== persistedPeriod
    || String(cadence) !== String(assignment.decision_cadence || '')
    || String(thinking) !== String(assignment.thinking_mode || 'fast');
  useEffect(() => {
    let alive = true;
    if (thinking !== 'deep' || legacy) {
      setDeepAuth(null); setAuthLoading(false); setAuthNote('');
      return () => { alive = false; };
    }
    setAuthLoading(true); setAuthNote('');
    appDeepAuthRequest(meta.code, 'read').then(r => {
      if (!alive) return;
      setDeepAuth(r); setAuthLoading(false);
    });
    return () => { alive = false; };
  }, [meta.code, thinking, legacy]);
  const refreshDeepAuth = async (fallback) => {
    const refreshed = await appDeepAuthRequest(meta.code, 'read');
    setDeepAuth(refreshed && refreshed.supported !== null ? refreshed : fallback);
  };
  const authorizeSession = async () => {
    if (authBusy || authLoading || !deepAuth || deepAuth.supported !== true || deepAuth.active
      || deepAuth.eligible === false || deepAuth.periodSupported === false || draftDirty
      || deepAuth.status === 'exhausted') return;
    if (deepAuth.revision == null) { setAuthNote('后端未返回授权版本，未发起授权。'); return; }
    if (!deepAuth.requiredConfirmText) { setAuthNote('后端未返回必需确认文案，未发起授权。'); return; }
    const entered = window.prompt('请逐字输入以下服务端确认文字：\n' + deepAuth.requiredConfirmText
      + '\n\n仅研究、不交易；本次授权最多 1 次深研 / 20 次模型调用（8 次 clean path；Technical/News 各一额外周期；每周期至多一次结构重采样）。', '');
    if (entered == null) return;
    if (entered !== deepAuth.requiredConfirmText) { setAuthNote('确认文字不一致，未发起授权。'); return; }
    setAuthBusy(true); setAuthNote('');
    const r = await appDeepAuthRequest(meta.code, 'authorize', deepAuth, entered);
    if (r && r.ok) {
      setAuthNote('本交易时段授权已提交。');
      await refreshDeepAuth(r);
    } else {
      setDeepAuth(r || deepAuth); setAuthNote(appDeepAuthReasonCN(r && r.reason) || '授权失败');
    }
    setAuthBusy(false);
  };
  const revokeSession = async () => {
    if (authBusy || authLoading || !deepAuth || !deepAuth.active || !deepAuth.authorizationId) return;
    if (!window.confirm('确认撤销本票当前交易时段的 Pro·10席事件深研授权？')) return;
    setAuthBusy(true); setAuthNote('');
    const r = await appDeepAuthRequest(meta.code, 'revoke', deepAuth);
    if (r && r.ok) {
      setAuthNote('授权已撤销。');
      await refreshDeepAuth(r);
    } else {
      setDeepAuth(r || deepAuth); setAuthNote(appDeepAuthReasonCN(r && r.reason) || '撤销失败');
    }
    setAuthBusy(false);
  };
  const save = async () => {
    if (saving || unavailable) return;
    setSaving(true); setError('');
    const r = await onSave({ enabled, strategy_id: strategyId, evidence_tf: evidenceTF, decision_cadence: cadence, thinking_mode: thinking });
    setSaving(false);
    if (!r || !r.ok) { setError((r && r.reason) || '保存失败'); return; }
    onClose();
  };
  const selStyle = { width: '100%', fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink)', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 7, padding: '6px 8px', outline: 'none' };
  const row = (label, child, note) => (
    <label style={{ display: 'grid', gridTemplateColumns: '90px 1fr', gap: 9, alignItems: 'center' }}>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{label}</span>
      <span>{child}{note && <span className="serif" style={{ display: 'block', marginTop: 3, fontSize: 9, color: 'var(--ink-3)' }}>{note}</span>}</span>
    </label>
  );
  const authStatus = (() => {
    if (authLoading) return '读取中…';
    if (!deepAuth) return '尚未读取';
    if (deepAuth.supported === false) return '接口未接通';
    if (deepAuth.supported == null) return '接口不可达';
    if (deepAuth.status === 'not_yet_valid') return '已预授权 · 待生效';
    if (deepAuth.status === 'active') return '授权有效';
    if (deepAuth.status === 'stale') return '旧授权待撤销';
    if (deepAuth.status === 'exhausted') return '额度已用尽';
    if (deepAuth.status === 'expired') return '已过期';
    if (deepAuth.status === 'revoked') return '已撤销';
    if (deepAuth.active) return '已授权';
    return deepAuth.status && deepAuth.status !== 'none' ? deepAuth.status : '未授权';
  })();
  const authPeriod = (deepAuth && deepAuth.period) || evidenceTF;
  const authPeriodCN = APP_TF_CN[authPeriod] || authPeriod || '—';
  const sessionLabel = deepAuth && deepAuth.sessionLabel ? deepAuth.sessionLabel : '';
  const authorizeLabel = sessionLabel && /下一交易日/.test(sessionLabel) ? '授权下一交易日' : '授权本交易时段';
  const validUntil = deepAuth && deepAuth.validUntil ? (() => {
    const d = new Date(deepAuth.validUntil);
    return isNaN(d.getTime()) ? String(deepAuth.validUntil) : d.toLocaleString('zh-CN', { hour12: false });
  })() : '—';
  const authBlocked = authBusy || authLoading || !deepAuth || deepAuth.supported !== true || deepAuth.active
    || deepAuth.eligible === false || deepAuth.periodSupported === false || draftDirty
    || deepAuth.status === 'exhausted'
    || deepAuth.revision == null || !deepAuth.requiredConfirmText;
  const periodNote = periodDirty ? ('周期尚未保存：当前服务端为 ' + (APP_TF_CN[persistedPeriod] || persistedPeriod)
    + '，请先保存 ' + (APP_TF_CN[evidenceTF] || evidenceTF) + ' 后再授权；不会退回日线。')
    : '';
  const draftNote = draftDirty ? '当前有未保存的盯盘配置（启用状态、Agent、周期、节奏或研判方式）。请先保存，再重新进入授权；授权只绑定已落盘配置。' : '';
  const grantedLabel = deepAuth && deepAuth.status === 'not_yet_valid' ? '已预授权 · 待生效'
    : (deepAuth && deepAuth.status === 'stale' ? '旧授权待撤销'
      : (sessionLabel ? '已授权 · ' + sessionLabel : '本交易时段已授权'));
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 120, background: 'rgba(28,24,20,0.28)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
      <div onClick={e => e.stopPropagation()} style={{ width: 500, maxWidth: '94vw', maxHeight: '90vh', overflowY: 'auto', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 14, boxShadow: '0 16px 44px rgba(28,24,20,0.24)', padding: '17px 19px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span className="seal serif" style={{ width: 27, height: 27, fontSize: 14 }}>盯</span>
          <div><div className="serif" style={{ fontSize: 16, fontWeight: 700 }}>{meta.name} · 自动研判配置</div><div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 2 }}>{meta.code}</div></div>
          <span onClick={onClose} style={{ marginLeft: 'auto', cursor: 'pointer', color: 'var(--ink-3)', fontSize: 18 }}>×</span>
        </div>
        <div className="mono" style={{ marginTop: 12, padding: '7px 9px', borderRadius: 7, background: legacy ? 'rgba(138,111,63,0.07)' : (unavailable ? 'rgba(168,57,45,0.06)' : 'rgba(74,107,92,0.06)'), color: legacy || unavailable ? 'var(--ink-2)' : 'var(--dai)', fontSize: 9.5, lineHeight: 1.55 }}>
          {legacy ? '旧版绑定模式：只能明确纳入/移出；Agent、分钟口径和节奏不会假装已应用。' : (unavailable ? ((assignmentMode && assignmentMode.reason) || '单票配置接口暂不可达') + '。为防误改策略，本次不能保存，也不会回退旧 bind。' : '单票 assignment 已接通。策略适用范围与自动盯盘状态彼此独立。')}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 11, marginTop: 14, opacity: unavailable ? 0.65 : 1 }}>
          {row('自动研判', <span onClick={() => !unavailable && setEnabled(v => !v)} className="mono" style={{ display: 'inline-block', cursor: unavailable ? 'default' : 'pointer', padding: '4px 10px', borderRadius: 9, border: '1px solid ' + (enabled ? 'var(--yin)' : 'var(--line)'), background: enabled ? 'var(--yin)' : 'transparent', color: enabled ? 'var(--paper)' : 'var(--ink-3)', fontSize: 10 }}>{enabled ? '● 已纳入' : '○ 未纳入'}</span>)}
          {row('负责 Agent', <select disabled={legacy || unavailable} value={legacy ? initialSid : strategyId} onChange={e => setStrategyId(e.target.value)} style={selStyle}>
            {!legacy && <option value="__dedicated__">新建「{meta.name} · 动量盯盘」(推荐)</option>}
            {(strategies || []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            {initialSid && initialSid !== '__dedicated__' && !(strategies || []).some(s => s.id === initialSid) && <option value={initialSid}>{initialSid}</option>}
          </select>, legacy ? '旧后端继续使用已有 bind，不会在这里暗换策略。' : '新建专属 Agent 会先持久化策略，再提交 assignment。')}
          {row('研判数据周期', <select disabled={legacy || unavailable} value={legacy ? 'day' : evidenceTF} onChange={e => setEvidenceTF(e.target.value)} style={selStyle}>
            <option value="day">日线</option><option value="30min">30分钟</option><option value="5min">5分钟</option><option value="1min">1分钟</option>
          </select>, legacy ? '旧后端固定日线。' : '若所选分钟内核尚未接通，服务端会暂停，不会暗退日线。')}
          {row('自动研判节奏', <select disabled={legacy || unavailable} value={legacy ? '60min' : cadence} onChange={e => setCadence(e.target.value)} style={selStyle}>
            <option value="10min">每10分钟</option><option value="30min">每30分钟</option><option value="60min">每60分钟</option><option value="daily">每日一次</option>
          </select>)}
          {row('自动研判方式', <select disabled={legacy || unavailable} value={legacy ? 'fast' : thinking} onChange={e => setThinking(e.target.value)} style={selStyle}>
            <option value="fast">快速研判</option><option value="deep">Pro · 10席事件深研</option>
          </select>, legacy ? '旧后端固定快速研判。' : 'Pro 仍先快速盯盘；仅命中事件且对应交易时段授权有效时，才进入10席深研。')}
          {thinking === 'deep' && !legacy && <div style={{ border: '1px solid var(--zhu-soft)', borderRadius: 10, padding: '10px 11px', background: 'rgba(168,57,45,0.035)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
              <span className="serif" style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--ink-1)' }}>Pro · 10席事件深研授权</span>
              <span className="mono" style={{ fontSize: 8.5, border: '1px solid var(--line)', borderRadius: 5, padding: '1px 6px', color: deepAuth && deepAuth.active ? 'var(--dai)' : 'var(--ink-3)' }}>状态 · {authStatus}</span>
            </div>
            <div className="mono" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 12px', marginTop: 8, fontSize: 9, color: 'var(--ink-2)', lineHeight: 1.5 }}>
              <span>周期资格 · {authPeriodCN} · {deepAuth && (deepAuth.eligible === false || deepAuth.periodSupported === false) ? '不可授权' : (deepAuth && (deepAuth.eligible === true || deepAuth.periodSupported === true) ? '可用' : '待服务端确认')}</span>
              <span>授权时段 · {sessionLabel || '本交易时段'}</span>
              <span>剩余次数 · {deepAuth && deepAuth.runsRemaining != null ? deepAuth.runsRemaining : '—'} 次</span>
              <span>模型调用余量 · {deepAuth && deepAuth.llmRemaining != null ? deepAuth.llmRemaining : '—'} 次</span>
              <span>有效期 · {validUntil}</span>
            </div>
            <div className="mono" style={{ marginTop: 5, fontSize: 8.5, color: 'var(--ink-3)' }}>服务端状态 · {deepAuth && deepAuth.status ? deepAuth.status : '—'} · 授权资格 {deepAuth && deepAuth.eligible != null ? (deepAuth.eligible ? '是' : '否') : '未返回'} · 周期支持 {deepAuth && deepAuth.periodSupported != null ? (deepAuth.periodSupported ? '是' : '否') : '见资格说明'}</div>
            <div className="mono" style={{ marginTop: 5, fontSize: 8.5, color: 'var(--ink-3)' }}>全局深研余量 · {deepAuth && deepAuth.globalRemaining != null ? deepAuth.globalRemaining + ' 次' : (deepAuth && deepAuth.globalBudgetText ? deepAuth.globalBudgetText : '—')}{deepAuth && deepAuth.globalLLMRemaining != null ? ' · 模型调用 ' + deepAuth.globalLLMRemaining + ' 次' : ''}</div>
            <div className="mono" style={{ marginTop: 5, fontSize: 8.5, color: 'var(--ink-3)' }}>10席深研支持周期 · {deepAuth && deepAuth.supportedPeriods && deepAuth.supportedPeriods.length > 0 ? deepAuth.supportedPeriods.map(p => APP_TF_CN[p] || p).join('、') : '日线（当前首版）'} · 分钟周期不回退日线</div>
            <div className="serif" style={{ marginTop: 7, fontSize: 9.5, color: 'var(--yin)', lineHeight: 1.55 }}>仅研究、不交易 · 每个授权交易时段最多 1 次深研 / 20 次模型调用（8 次 clean path；Technical/News 各一额外周期；每周期至多一次结构重采样）；选择 Pro 或保存配置都不会自动授权。</div>
            {draftNote && <div className="serif" style={{ marginTop: 5, fontSize: 9.5, color: 'var(--yin)', lineHeight: 1.55 }}>{draftNote}</div>}
            {periodNote && <div className="serif" style={{ marginTop: 5, fontSize: 9.5, color: 'var(--yin)', lineHeight: 1.55 }}>{periodNote}</div>}
            {deepAuth && (deepAuth.reason || deepAuth.detail) && <div className="mono" style={{ marginTop: 5, fontSize: 8.7, color: 'var(--yin)', whiteSpace: 'pre-wrap' }}>{appDeepAuthReasonCN(deepAuth.reason)}{deepAuth.detail ? (deepAuth.reason ? ' · ' : '') + deepAuth.detail : ''}</div>}
            {authNote && <div className="mono" style={{ marginTop: 5, fontSize: 8.7, color: 'var(--yin)', whiteSpace: 'pre-wrap' }}>{authNote}</div>}
            <div style={{ display: 'flex', gap: 7, marginTop: 9 }}>
              <span onClick={authorizeSession} className="serif" style={{ cursor: authBlocked ? 'default' : 'pointer', borderRadius: 7, padding: '5px 11px', background: authBlocked ? 'var(--line)' : 'var(--yin)', color: authBlocked ? 'var(--ink-3)' : 'var(--paper)', fontSize: 10.5 }}>{authBusy ? '处理中…' : (deepAuth && deepAuth.active ? grantedLabel : authorizeLabel)}</span>
              <span onClick={revokeSession} className="serif" style={{ cursor: deepAuth && deepAuth.active && deepAuth.authorizationId && !authBusy ? 'pointer' : 'default', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', color: deepAuth && deepAuth.active ? 'var(--ink-2)' : 'var(--ink-3)', opacity: deepAuth && deepAuth.active ? 1 : 0.5, fontSize: 10.5 }}>撤销</span>
            </div>
          </div>}
        </div>
        {assignment && assignment.effective_evidence_tf == null && <div className="serif" style={{ marginTop: 10, color: 'var(--yin)', fontSize: 10.5, lineHeight: 1.55 }}>当前请求 {APP_TF_CN[assignment.requested_evidence_tf || assignment.evidence_tf] || assignment.evidence_tf}，后端状态为“未接通”；该票已暂停自动研判，没有退回日线。</div>}
        {error && <div className="mono" style={{ marginTop: 10, color: 'var(--yin)', fontSize: 9.5 }}>{error}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <span onClick={onClose} className="serif" style={{ cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 8, padding: '7px 15px', color: 'var(--ink-2)', fontSize: 11.5 }}>取消</span>
          <span onClick={save} className="serif" style={{ cursor: unavailable || saving ? 'default' : 'pointer', borderRadius: 8, padding: '7px 17px', background: unavailable || saving ? 'var(--line)' : 'var(--yin)', color: unavailable || saving ? 'var(--ink-3)' : 'var(--paper)', fontSize: 11.5 }}>{saving ? '保存中…' : (legacy ? '确认旧版绑定' : '保存配置')}</span>
        </div>
      </div>
    </div>
  );
}

function LuoziApp() {
  const [page, setPage] = useState('today');          // today | replay | strategy(单轴,三轴退役)
  const [code, setCode] = useState(window.LZ_PRIMARY);
  const [strategies, setStrategies] = useState(() => window.lzStrategyForCode ? window.lzStrategyForCode(window.LZ_PRIMARY) : []);
  const [curStratId, setCurStratId] = useState(null); // 当前策略 id(null/失效 → curSid 回退本票首个策略)
  const [selected, setSelected] = useState(null);     // 复盘:选中的 run 决策(RunDecCard)
  const [tf, setTf] = useState('D');
  const [dark, setDark] = useState(false);
  const [toast, setToast] = useState(null);
  const [, setPoolTick] = useState(0);                // 票池增删后强制重渲染(池数组是模块级)
  const [poolTickV, setPoolTickV] = useState(0);      // 传给 TicketList 触发其轮询重建
  const [realSyms, setRealSyms] = useState({});       // code → 用真日K装配的 symbol(覆盖合成)
  const bars1SeqRef = useRef(0);                      // 1min 请求序；切票/重拉只收最新同码响应
  const [dataMode, setDataMode] = useState('mock');   // real | mock
  const [zoom, setZoom] = useState(null);
  const [panEnd, setPanEnd] = useState(null);
  const [market, setMarket] = useState(null);         // 今日市场状态(/watch/market_status)
  const [quote, setQuote] = useState(null);           // 实时盘口(仅今日页轮询)
  const [book, setBook] = useState(null);
  const [ticks, setTicks] = useState(null);
  const [newsKw, setNewsKw] = useState('');
  const [newsPayload, setNewsPayload] = useState(null);
  const [newsPanel, setNewsPanel] = useState(null);
  const [liveBar, setLiveBar] = useState(null);       // 今日柱缓存(盘后保图有今天)
  useEffect(() => { setLiveBar(window.lzLivebarLoad ? window.lzLivebarLoad(code) : null); }, [code]);
  const [watch, setWatch] = useState(null);           // 后端盯盘状态(null=端点不可达/旧后端)
  // 行情端点的 session state 更新更快；缺席时才回退 30s 的 watcher 心跳。
  const liveMarketOpen = quote && typeof quote.marketOpen === 'boolean'
    ? quote.marketOpen : (watch ? !!watch.marketOpen : null);
  const [watchAssignments, setWatchAssignments] = useState({});
  const [assignmentMode, setAssignmentMode] = useState({ supported: null, legacy: false, reason: '检查单票配置中' });
  const [watchEdit, setWatchEdit] = useState(null);   // 正在配置的股票 meta
  const assignmentWriteRef = useRef({});              // PUT 后短窗写栅栏，防轮询旧快照覆盖刚保存配置
  const [todayDecs, setTodayDecs] = useState([]);     // 今日页 K 线金框标记源(后端落盘时间线)
  const [realDecs, setRealDecs] = useState({});       // 复盘真跑「进行中」的即时标记(跑完由选中 run 接管)
  const [realRun, setRealRun] = useState({ running: false, stopping: false, done: 0, total: 0, cur: null, seatName: '', errors: 0 });
  const [runsBump, setRunsBump] = useState(0);
  const [selRun, setSelRun] = useState(null);
  const [runDecs, setRunDecs] = useState([]);
  const [orderTriggers, setOrderTriggers] = useState([]);   // 条件单触发(标 K 线;不再写影子/台账)
  const [todayTool, setTodayTool] = useState('judge');       // 今日操作台:judge | order | trail
  const [todayToolOpen, setTodayToolOpen] = useState(false);
  const [todayTrailBump, setTodayTrailBump] = useState(0);
  // 复盘向导(与游标解耦的真跑参数;空 = 用派生默认:近 120 交易日 · 日线)
  const [wizStart, setWizStart] = useState('');
  const [wizEnd, setWizEnd] = useState('');
  const [wizFreq, setWizFreq] = useState('day');      // day | 30min
  const realStopRef = useRef(false);
  const realRunActiveRef = useRef(false);              // React state 提交前也立即加锁,防双击并发起两个 run
  useEffect(() => { realStopRef.current = true; }, [code, page]);   // 切票/切页 → 停掉进行中的真跑

  useEffect(() => { document.body.classList.toggle('dark', dark); }, [dark]);
  useEffect(() => {
    if (!todayToolOpen) return;
    const closeOnEsc = (e) => { if (e.key === 'Escape') setTodayToolOpen(false); };
    window.addEventListener('keydown', closeOnEsc);
    return () => window.removeEventListener('keydown', closeOnEsc);
  }, [todayToolOpen]);

  // 当前票在场策略(订阅 GL,增删改 / 跨标签即刷新)
  useEffect(() => {
    setCurStratId(null);
    const refresh = () => setStrategies(window.lzStrategyForCode ? window.lzStrategyForCode(code) : []);
    refresh();
    const off = window.GL ? window.GL.on(refresh) : null;
    return () => { if (off) off(); };
  }, [code]);

  // P1⑨:接 cockpit 交棒(选股「据此落子」篮子 / 帷幄研判 / 图谱跳转)。通知条直插 DOM(effect 风暴下 React toast 不显形)。
  useEffect(() => {
    const notice = (text) => {
      try {
        const d = document.createElement('div');
        d.id = 'lz-handoff-notice';
        d.style.cssText = 'position:fixed;bottom:70px;left:50%;transform:translateX(-50%);z-index:9500;display:flex;align-items:center;gap:11px;background:var(--paper,#f1ead9);border:1px solid var(--dai-soft,#9db4a8);border-radius:11px;padding:11px 16px;box-shadow:0 6px 26px rgba(28,24,20,0.18);font-family:var(--serif,serif);font-size:12.5px;color:var(--ink-1,#3a332b);max-width:72vw;';
        d.innerHTML = '<span style="width:22px;height:22px;font-size:12px;background:var(--dai,#4a6b5c);color:var(--paper,#f1ead9);display:flex;align-items:center;justify-content:center;border-radius:6px;flex-shrink:0">瀾</span><span></span><span style="cursor:pointer;color:var(--ink-3,#999);font-size:14px;padding:0 2px;flex-shrink:0">×</span>';
        d.children[1].textContent = text;
        d.lastChild.onclick = () => { try { d.remove(); } catch (e) {} };
        document.body.appendChild(d);
      } catch (e) {}
    };
    const tick = setTimeout(() => {
      const h = window.GL && window.GL.take ? window.GL.take('cockpit', WW_WS) : null;
      if (!h) return;
      const bare = (c) => String(c || '').replace(/^(SH|SZ|BJ)/i, '');
      if (h.fromScreen && Array.isArray(h.basket) && h.basket.length) {
        let added = 0;
        h.basket.forEach(b => {
          if (b && window.lzPoolAdd && window.lzPoolAdd({ code: bare(b.code), name: b.name, ind: b.ind })) added++;
        });
        if (added) { setPoolTick(t => t + 1); setPoolTickV(t => t + 1); }
        const first = h.basket.find(b => b && window.LZ_SYMBOLS && window.LZ_SYMBOLS[bare(b.code)]);
        if (first) { setCode(bare(first.code)); setPage('today'); }
        const names = h.basket.slice(0, 5).map(b => (b.name || b.code)).join('、') + (h.basket.length > 5 ? '…' : '');
        notice('已接收选股篮子 ' + h.basket.length + ' 只(' + names + '):新入票池 ' + added + ' 只'
          + (first ? ',已聚焦 ' + (first.name || first.code) : '') + ';左栏可切换,动态票可「移出票池」');
      } else if (h.code) {
        const c = bare(h.code);
        const added = !!(window.lzPoolAdd && window.lzPoolAdd({ code: c, name: h.name }));
        if (added) { setPoolTick(t => t + 1); setPoolTickV(t => t + 1); }
        const focusable = !!(window.LZ_SYMBOLS && window.LZ_SYMBOLS[c]);
        if (focusable) { setCode(c); setPage('today'); }
        notice('已接收帷幄研判交棒 ' + (h.name || c) + (added ? ':新入票池' : '') + (focusable ? ',已聚焦' : ''));
      } else if (h.focusSeat || h.focusId) {
        notice('来自研究图谱的跳转已接收(' + (h.focusSeat || h.focusId) + ')');
      }
    }, 600);
    return () => clearTimeout(tick);
  }, []);

  // 今日市场状态(快照,拉一次)
  useEffect(() => {
    let alive = true;
    if (window.lzFetchMarketStatus) window.lzFetchMarketStatus().then(m => { if (alive && m) setMarket(m); });
    return () => { alive = false; };
  }, []);

  // 后端盯盘状态:今日页 30s 轮询(端点不可达 → null 诚实态)
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchWatchStatus) { return; }
    let alive = true;
    const pull = () => window.lzFetchWatchStatus().then(w => { if (alive) setWatch(w); });
    pull();
    const iv = setInterval(pull, 30000);
    return () => { alive = false; clearInterval(iv); };
  }, [page]);
  const toggleWatch = (on) => {
    if (!window.lzToggleWatch) return Promise.resolve(null);
    return window.lzToggleWatch(on).then(w => { if (w) setWatch(w); return w; });
  };

  // 单票 assignments:与总闸同为今日页控制面，30s 刷新。只有明确 404/405 才进入 legacy；
  // 网络抖动/5xx 只显示不可达，绝不调用会改多个 bind 的旧 watchSet。
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchWatchAssignments) return;
    let alive = true;
    const pull = () => window.lzFetchWatchAssignments().then(snap => {
      if (!alive || !snap || snap.stale) return;
      setAssignmentMode({ supported: snap.supported, legacy: !!snap.legacy, reason: snap.reason || '' });
      if (snap.supported === true) {
        const now = Date.now();
        setWatchAssignments(prev => {
          const next = Object.assign({}, snap.assignments || {});
          const rev = a => (a && a.revision != null && isFinite(+a.revision)) ? +a.revision : null;
          // 慢 GET 可能在 PUT 之前发出、很久以后才回来；revision 单调时永远保留较新行，
          // 不能只靠几秒钟时间窗，否则 >10s 的旧响应仍会回滚刚保存的配置。
          Object.keys(prev || {}).forEach(c => {
            const pr = rev(prev[c]), nr = rev(next[c]);
            if (pr != null && (nr == null || pr > nr)) next[c] = prev[c];
          });
          Object.keys(assignmentWriteRef.current).forEach(c => {
            const fence = assignmentWriteRef.current[c];
            const fr = rev(fence && fence.assignment), nr = rev(next[c]);
            if (fence && fr != null && (nr == null || fr > nr)) next[c] = fence.assignment;
            else if (fence && fr == null && now - fence.at < 10000) next[c] = fence.assignment;
            else delete assignmentWriteRef.current[c];
          });
          return next;
        });
      }
    });
    pull(); const iv = setInterval(pull, 30000);
    return () => { alive = false; clearInterval(iv); };
  }, [page]);

  // 今日页 K 线金框标记:后端落盘研判(45s;含 watcher/手动;条件单不上 K 线金框——触发另有金环)
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchDecisionsTimeline) { setTodayDecs([]); return; }
    let alive = true;
    const pull = () => window.lzFetchDecisionsTimeline(code, 20).then(rows => {
      if (!alive || !rows) return;
      setTodayDecs(rows.filter(r => r.kind === 'decide').map(r => ({
        key: 'tl_' + r.id, seat: r.strategy_id || '', date: String(r.asof || r.ts || '').slice(0, 10),
        side: /买/.test(r.direction || '') ? 'buy' : (/卖/.test(r.direction || '') ? 'sell' : 'watch'),
        direction: r.direction || '', conf: (r.confidence != null ? r.confidence : null),
        rationale: r.rationale || '', asof: r.asof || r.ts, model_name: r.model_name || '',
        source: r.source || '', preset_id: r.preset_id || '', badges: r.badges || [],
        triggers: r.triggers || [], escalation_triggers: r.escalation_triggers || [],
        trigger_summary: r.trigger_summary || '', escalation_digest: r.escalation_digest || '',
        node_summaries: r.node_summaries || null, agent_summaries: r.agent_summaries || null,
        node_statuses: r.node_statuses || null, orchestration: r.orchestration || null,
        deep_quality: r.deep_quality || null, evidence_warnings: r.evidence_warnings || null,
        confidence_band: r.confidence_band || '',
        orchestration_mode: r.orchestration_mode || '', orchestration_grade: r.orchestration_grade || '',
        deep_variant: r.deep_variant || '', deep_status: r.deep_status || '', coverage: r.coverage || '',
        evidence_note: r.evidence_note || '',
        // 出场语义(2026-08-03):持仓语境的研判才有,缺席即 null(空仓行逐字同旧)
        position_action: r.position_action || null,
        exit_fraction: (r.exit_fraction == null ? null : +r.exit_fraction),
        exit_engine: r.exit_engine || null, t_ref: r.t_ref || null,
      })));
    });
    pull();
    const iv = setInterval(pull, 45000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, code]);

  // 实时盘口:只在交易中保持 6s 轮询；午休/盘后只取一次快照，30s 状态心跳负责开盘唤醒。
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchQuote) { setQuote(null); return; }
    let alive = true;
    const pull = () => window.lzFetchQuote(code).then(q => { if (alive && q) setQuote(q); });
    pull();
    if (liveMarketOpen !== true) return () => { alive = false; };
    const iv = setInterval(pull, 6000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, code, liveMarketOpen]);

  // 五档盘口 + 逐笔:盘中 8s；休市保留最后快照且不再请求。
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchOrderbook) { setBook(null); setTicks(null); return; }
    let alive = true;
    const pull = () => {
      window.lzFetchOrderbook(code).then(b => { if (alive && b) setBook(b); });
      window.lzFetchTicks(code, 30).then(t => { if (alive && t) setTicks(t); });
    };
    pull();
    if (liveMarketOpen !== true) return () => { alive = false; };
    const iv = setInterval(pull, 8000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, code, liveMarketOpen]);

  const currentAssignment = watchAssignments[String(code).replace(/^(SH|SZ|BJ)/i, '')] || null;
  const availableStrategies = useMemo(() => {
    const out = (strategies || []).slice();
    const sid = currentAssignment && currentAssignment.strategy_id;
    const assigned = sid && window.lzStrategyGet ? window.lzStrategyGet(sid) : null;
    if (assigned && !out.some(s => s.id === assigned.id)) out.unshift(assigned);
    return out;
  }, [strategies, currentAssignment && currentAssignment.strategy_id]);

  // 第3期:symbol 按 (bars, strategies) 反应式重建。实时 1min/5min 都属于显示层，
  // 必须随重建后的 symbol 一起保留；此前只带 bars5，导致 bars1 已拉到 state 却被丢掉。
  const baseBars = (realSyms[code] && realSyms[code].bars) || window.LZ_SYMBOLS[code].bars;
  const _meta = window.LZ_SYMBOLS[code].meta;
  const symbol = useMemo(() => {
    const s = window.lzBuildSymbolFromBars(_meta, baseBars, availableStrategies,
      (realSyms[code] && realSyms[code].benchRows) || null);
    const live = realSyms[code] || {};
    return Object.assign({}, s,
      live.bars5 ? { bars5: live.bars5 } : {},
      live.bars1 ? { bars1: live.bars1 } : {});
  }, [baseBars, availableStrategies, code, realSyms]);
  const assignedSid = currentAssignment && currentAssignment.strategy_id;
  const curSid = (page === 'today' && assignedSid && availableStrategies.some(s => s.id === assignedSid)) ? assignedSid
    : ((curStratId && availableStrategies.some(s => s.id === curStratId)) ? curStratId
      : ((assignedSid && availableStrategies.some(s => s.id === assignedSid)) ? assignedSid : ((availableStrategies[0] && availableStrategies[0].id) || null)));
  const curStrat = availableStrategies.find(s => s.id === curSid) || null;
  const curName = (curStrat && curStrat.name) || '策略';
  const toggleTodayTool = (tool) => {
    const next = tool || todayTool;
    if (todayToolOpen && next === todayTool) setTodayToolOpen(false);
    else { setTodayTool(next); setTodayToolOpen(true); }
  };
  const currentOrderTriggerPrefix = String(code || '') + '·' + String(curSid || '');
  const currentOrderTriggers = orderTriggers.filter(t => String((t && t.id) || '').startsWith(currentOrderTriggerPrefix));
  const currentOrderTriggered = currentOrderTriggers.length > 0;
  const workbenchTabs = [['judge', '研判'], ['order', '条件单'], ['trail', '记录']];
  const active = useMemo(() => curSid ? [curSid] : [], [curSid]);
  const n = symbol.bars.length;
  const cursor = n - 1;                                // 游标退役:恒末根(推演/播放已删)

  const saveWatchConfig = async (draft) => {
    if (!watchEdit) return { ok: false, reason: '未选择股票' };
    const c = String(watchEdit.code || '').replace(/^(SH|SZ|BJ)/i, '');
    // 只有 GET 明确 404/405 后才允许旧版 watchSet；网络/5xx/unavailable 在弹层中禁存。
    if (assignmentMode.legacy) {
      if (!window.lzWatchSet) return { ok: false, reason: '旧版绑定函数不可用' };
      const ok = window.lzWatchSet(c, !!draft.enabled);
      if (!ok && draft.enabled) return { ok: false, reason: '没有可绑定的旧版策略' };
      setPoolTickV(v => v + 1);
      setToast({ kind: 'handoff', text: '已按旧版绑定' + (draft.enabled ? '纳入 ' : '移出 ') + watchEdit.name + '；研判仍为日线，未应用分钟口径。' });
      setTimeout(() => setToast(null), 5200);
      return { ok: true, legacy: true };
    }
    if (assignmentMode.supported !== true || !window.lzPutWatchAssignment) {
      return { ok: false, reason: assignmentMode.reason || '单票配置接口不可达' };
    }
    let sid = draft.strategy_id === '__dedicated__' ? '' : String(draft.strategy_id || '');
    if (draft.enabled) {
      if (!sid) {
        if (!window.lzEnsureDedicatedWatchStrategy) return { ok: false, reason: '专属策略创建器不可用' };
        const made = await window.lzEnsureDedicatedWatchStrategy(watchEdit, draft);
        if (!made || !made.ok) return made || { ok: false, reason: '专属策略创建失败' };
        sid = made.strategy.id;
      } else {
        const st = window.lzStrategyGet ? window.lzStrategyGet(sid) : null;
        if (st && window.lzPersistWatchStrategy) {
          const persisted = await window.lzPersistWatchStrategy(st);
          if (!persisted || !persisted.ok) return persisted || { ok: false, reason: '策略持久化失败' };
        } else if (!(currentAssignment && currentAssignment.strategy_id === sid)) {
          return { ok: false, reason: '所选策略尚未加载，不能安全指派' };
        }
      }
    } else if (!sid && currentAssignment) sid = currentAssignment.strategy_id || '';
    const body = { enabled: !!draft.enabled, strategy_id: sid,
      evidence_tf: draft.evidence_tf, decision_cadence: draft.decision_cadence,
      thinking_mode: draft.thinking_mode };
    if (currentAssignment && currentAssignment.revision != null) body.revision = currentAssignment.revision;
    const saved = await window.lzPutWatchAssignment(c, body);
    if (!saved || !saved.ok) {
      if (saved && saved.legacy) setAssignmentMode({ supported: false, legacy: true, reason: saved.reason || '旧后端' });
      return saved || { ok: false, reason: '保存失败' };
    }
    const a = saved.assignment;
    assignmentWriteRef.current[c] = { at: Date.now(), assignment: a };
    setWatchAssignments(prev => Object.assign({}, prev, { [c]: a }));
    if (window.lzFetchWatchStatus) window.lzFetchWatchStatus().then(w => { if (w) setWatch(w); });
    const unsupported = a && a.effective_evidence_tf == null;
    setToast({ kind: 'handoff', text: unsupported
      ? ('已保存 ' + watchEdit.name + ' 的 ' + (APP_TF_CN[a.requested_evidence_tf] || a.requested_evidence_tf) + ' 请求；内核尚未接通，该票已暂停且未退回日线。')
      : ('已' + (a.enabled ? '纳入 ' : '停用 ') + watchEdit.name + ' · ' + (APP_TF_CN[a.requested_evidence_tf] || a.requested_evidence_tf) + ' · ' + (APP_CADENCE_CN[a.decision_cadence] || a.decision_cadence)) });
    setTimeout(() => setToast(null), 6200);
    return { ok: true, assignment: a };
  };

  // ④-③ 捕获今日柱:今日页下报价晚于末根真日K日 → 提一根今日真实柱写缓存
  useEffect(() => {
    if (page !== 'today' || !quote || !window.lzLivebarFromQuote) return;
    const last = symbol.bars[n - 1];
    const nb = window.lzLivebarFromQuote(quote, last ? last.date : null);
    if (nb) { setLiveBar(nb); if (window.lzLivebarSave) window.lzLivebarSave(code, nb); }
  }, [quote, page, code, n]);

  // 复盘净值:只由选中 run 驱动(run 真决策模拟成交 + 该策略时钟止损/止盈/最长持有);未选 run → null 引导。
  //   策略已删则 clock=undefined → 回退旧口径(只认显式信号),诚实降级。
  const runClock = (selRun && window.lzStrategyGet && (window.lzStrategyGet(selRun.strategy_id) || {}).clock) || undefined;
  const runPerf = useMemo(() => {
    if (page !== 'replay' || !selRun || !window.lzRunBacktest) return null;
    const isMin = (selRun.tf === '30min');
    if (!isMin && tf !== 'D') return null;
    const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : symbol.bars;
    return window.lzRunBacktest(runDecs, refBars, false, runClock);
  }, [page, selRun, tf, runDecs, symbol, runClock]);
  const runPerfHybrid = useMemo(() => {
    if (page !== 'replay' || !selRun || !window.lzRunBacktest) return null;
    const isMin = (selRun.tf === '30min');
    if (!isMin && tf !== 'D') return null;
    const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : symbol.bars;
    return window.lzRunBacktest(runDecs, refBars, true, runClock);
  }, [page, selRun, tf, runDecs, symbol, runClock]);
  const repPerf = page === 'replay' ? runPerf : null;
  const repPerfHybrid = page === 'replay' ? runPerfHybrid : null;
  const anyHybrid = (runDecs || []).some(d => (d && (+d.w || 0) > 0));

  // ⑤++ 复盘「真跑」(向导驱动):按 [起, 止] 日期区间逐 bar 真调 /seats/decide(PIT),与游标解耦。
  //   运行中再点=停;串行 await;regime=null 防 look-ahead;跑完注册 run 头并自动选中。
  const runRealThink = async () => {
    if (realRunActiveRef.current) {
      if (!realStopRef.current) {
        realStopRef.current = true;
        setRealRun(s => Object.assign({}, s, { stopping: true }));
      }
      return;
    }
    if (page !== 'replay' || !window.lzSeatDecide) return;
    // ref 是同步互斥锁:同一帧内的第二次点击也只会发停止信号,不会再起一条并发 run。
    realRunActiveRef.current = true;
    realStopRef.current = false;
    setRealRun(s => Object.assign({}, s, { running: true, stopping: false, done: 0, total: 0, cur: null, errors: 0 }));
    try {
      await runRealThinkLocked();
    } catch (e) {
      setToast({ kind: 'handoff', text: '复盘真跑异常终止:' + String((e && e.message) || e || '未知错误') });
      setTimeout(() => setToast(null), 4800);
    } finally {
      realRunActiveRef.current = false;
      setRealRun(s => Object.assign({}, s, { running: false, stopping: false, cur: null }));
    }
  };

  const runRealThinkLocked = async () => {
    const isMin = (wizFreq === '30min');
    const sid = curSid;
    if (!sid) { setToast({ kind: 'handoff', text: '无策略可跑——先去「策略」页钤印一个' }); setTimeout(() => setToast(null), 4000); return; }
    const strat = window.lzStrategyGet ? window.lzStrategyGet(sid) : null;
    const tmpl = strat && strat.template;
    const seatName = (strat && strat.name) || sid;
    const tmplCreed = (window.LZ_TEMPLATES && tmpl && window.LZ_TEMPLATES[tmpl] && window.LZ_TEMPLATES[tmpl].creed) || '';
    const creed = (strat && strat.creed) || tmplCreed;
    const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(sid) : { cards: [], factors: [] };
    const codeNow = code, meta = symbol.meta;
    // 区间默认:近 120 交易日(日线)/ 近 10 交易日(30min);向导有值用向导
    const dayBars = symbol.bars;
    const defStart = dayBars[Math.max(0, dayBars.length - (isMin ? 10 : 120))].date;
    const defEnd = dayBars[dayBars.length - 1].date;
    const d0 = wizStart || defStart, d1 = wizEnd || defEnd;
    let runBars;
    if (isMin) {
      let bars5 = symbol.bars5;
      if (!bars5 && window.lzFetchBars5) {
        bars5 = await window.lzFetchBars5(codeNow, 2400);
        if (bars5) setRealSyms(s => { const cur = s[codeNow]; return cur ? Object.assign({}, s, { [codeNow]: Object.assign({}, cur, { bars5 }) }) : s; });
      }
      if (!bars5 || !bars5.length) { setToast({ kind: 'handoff', text: '30 分钟粒度需要真 5min 数据,当前未到——换日线或稍后再试' }); setTimeout(() => setToast(null), 4200); return; }
      runBars = window.lzBars30 ? window.lzBars30(Object.assign({}, symbol, { bars5 })) : [];
      runBars = runBars.filter(b => b.day >= d0 && b.day <= d1);
    } else {
      runBars = dayBars.filter(b => b.date >= d0 && b.date <= d1);
    }
    if (!runBars.length) { setToast({ kind: 'handoff', text: '区间内无 K 线(' + d0 + ' → ' + d1 + ')' }); setTimeout(() => setToast(null), 4000); return; }
    // 因子窗口热身:起点前至少留 6 根日K(与旧 startIdx=max(6,·) 同口径)
    if (!isMin) {
      const i0 = dayBars.findIndex(b => b.date === runBars[0].date);
      if (i0 >= 0 && i0 < 6) runBars = runBars.slice(6 - i0);
      if (!runBars.length) { setToast({ kind: 'handoff', text: '区间太靠前,因子窗口不足' }); setTimeout(() => setToast(null), 4000); return; }
    }
    if (runBars.length > LZ_RUN_RECONCILE_LIMIT) {
      setToast({ kind: 'handoff', text: '本次有 ' + runBars.length + ' 个研判点,超过单次 300 点的一致性上限——请缩短日期区间后再跑' });
      setTimeout(() => setToast(null), 5000);
      return;
    }
    const total = runBars.length;
    const runId = window.lzRunId ? window.lzRunId() : ('run_' + Date.now());
    setSelRun(null); setSelected(null);
    setRealDecs({});                                    // 进行中标记清零重来
    setRealRun({ running: true, stopping: !!realStopRef.current, done: 0, total: total, cur: 0, seatName: seatName, errors: 0 });
    let done = 0, errors = 0;
    // 持仓状态机(纯 LLM 口径,镜像 lzRunBacktest 的 pos/entryPx/entryIdx):只活在本次「真跑」作用域,
    //   不落盘不外泄——喂给 decide 的 hold_entry/hold_bars 只是上下文,后端不因此改输出 schema。
    let pos = 0, entryPx = null, entryIdx = null, posFrac = 0;   // posFrac:剩余仓位比例(减仓后 <1)
    for (let k = 0; k < total; k++) {
      if (realStopRef.current) break;
      const bar = runBars[k];
      if (!bar) continue;
      setRealRun(s => Object.assign({}, s, { cur: k }));
      let res = null;
      // 减仓后仍持仓(posFrac<1),持仓语境照传——入场锚不因减仓改变(与台账「减仓不改成本」同口径)
      const holdExtra = (pos === 1) ? { hold_entry: entryPx, hold_bars: k - entryIdx } : null;
      try {
        res = await window.lzSeatDecide({
          code: codeNow, name: meta.name, date: bar.date,
          seat_cn: seatName, creed: creed, mode: 'fast',
          strategy_id: sid, strategy_name: seatName,
          card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
          cards: rcp.cards, recipe_factors: rcp.factors,
          industry: meta.industry || '',
          regime: null,                                 // ★ 复盘 PIT:不喂今天市况;后端按 as-of 日产物补(防 look-ahead)
          run_id: runId,
          freq: isMin ? '30min' : 'day',
          w: (strat && strat.w) || 0,
          pa: !!(strat && strat.pa),
          pa_method: (strat && strat.pa) ? (strat.paMethod || window.LZ_PA_METHOD_DEFAULT || '') : '',
        }, holdExtra);
      } catch (e) { res = null; }
      // 不在 await 后 break:服务端会先落盘再回响应,此刻即使用户刚点了停止，也必须把这笔
      // 在途结果纳入即时 UI；停止只阻止「下一笔」发出。最终 run 头仍以后端回读为准。
      if (res && res.ok && res.direction) {
        done++;
        const dir = res.direction;
        const side = /买/.test(dir) ? 'buy' : (/卖/.test(dir) ? 'sell' : 'watch');
        // 了结比例(2026-08-03):后端持仓语境下 position_action=减仓 → exit_fraction=0.5、清仓 → 1.0;
        //   旧记录/空仓语境无此字段 → 1(全平),故旧行为逐位不变。
        const efRaw = +res.exit_fraction;
        const exitFrac = (side === 'sell' && isFinite(efRaw) && efRaw > 0 && efRaw < 1) ? efRaw : 1;
        // 状态机更新(纯 LLM side 驱动,不掺 hybrid w):买入建仓;卖出按比例了结——
        //   减仓后**仍在持仓**,故下一根 bar 依旧带 hold_entry 提问(否则会误以为已空仓而重新问「是否进场」)。
        if (side === 'buy' && pos === 0) { pos = 1; entryPx = +bar.c; entryIdx = k; posFrac = 1; }
        else if (side === 'sell' && pos === 1) {
          posFrac = +(posFrac * (1 - exitFrac)).toFixed(6);
          if (posFrac <= 1e-6) { pos = 0; entryPx = null; entryIdx = null; posFrac = 0; }
        }
        const rd = { key: 'true_' + sid + '@' + bar.date, seat: sid, code: codeNow, date: bar.date, side: side,
          direction: dir, conf: (res.confidence != null ? res.confidence : null),
          rationale: res.rationale || '', reasoning: res.reasoning || '', asof: res.asof || bar.date, model_name: res.model_name || '',
          // 出场语义原样带下去:回测按 exit_fraction 部分了结,卡片按 position_action 显形
          position_action: res.position_action || null,
          exit_fraction: (side === 'sell' ? exitFrac : null),
          exit_engine: res.exit_engine || null, t_ref: res.t_ref || null,
          workspace_history_ref: res.workspace_history_ref || null };
        setRealDecs(prev => {
          const arr = (prev[codeNow] || []).filter(x => x.key !== rd.key).concat([rd]);
          return Object.assign({}, prev, { [codeNow]: arr });
        });
      } else { errors++; }
      setRealRun(s => Object.assign({}, s, { done: done, errors: errors }));
    }
    // 服务端 /decide 在返回前已同步落盘；因此 run 头绝不再信任前端响应计数，而是完整回读
    // 同 run_id 的事实行后重算。回读失败宁可不注册头，也不制造「头 5 条、实际 6 条」分叉。
    let persistedRows;
    try {
      persistedRows = await lzReadRunRowsStrict(runId, codeNow, sid);
    } catch (e) {
      setToast({ kind: 'handoff', text: '研判请求已停止，但落盘核对失败；为避免错误汇总，本次未生成回测头，请刷新后重试' });
      setTimeout(() => setToast(null), 5200);
      return;
    }
    const summary = lzSummarizeRunRows(persistedRows);
    setRealRun(s => Object.assign({}, s, { done: summary.total, errors: errors }));
    if (!summary.total) return;

    const headPayload = {
      run_id: runId, code: codeNow, name: meta.name, strategy_id: sid, strategy_name: seatName,
      tf: isMin ? '30min' : 'D', start_date: summary.firstDate, end_date: summary.lastDate,
      n_buy: summary.nBuy, n_sell: summary.nSell, n_watch: summary.nWatch,
      n_err: errors, model: summary.lastModel || '', stopped: !!realStopRef.current,
    };
    try {
      const reg = await fetch((window.GUANLAN_BACKEND || '') + '/seats/runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(headPayload),
      });
      const regBody = await reg.json();
      if (!reg.ok || !regBody || !regBody.ok) throw new Error((regBody && regBody.reason) || ('HTTP ' + reg.status));
      setRunsBump(x => x + 1);
      const list = window.lzRunsList ? await window.lzRunsList(codeNow) : null;
      const head = (list && list.find(r => r.run_id === runId)) || Object.assign({ ts: new Date().toISOString() }, headPayload);
      setSelRun(head);
      setRealDecs({});
    } catch (e) {
      setToast({ kind: 'handoff', text: '研判已落盘并核对完成，但回测头注册失败；未展示不一致汇总，请稍后重试' });
      setTimeout(() => setToast(null), 5200);
    }
  };

  // 切页/切票 → 清选中与触发标记
  useEffect(() => { setSelected(null); setOrderTriggers([]); setSelRun(null); setRunDecs([]); }, [page, code]);
  // 切策略 → 清选中 run(回测历史从属于策略)
  useEffect(() => { setSelRun(null); setSelected(null); }, [curSid]);

  // 选中 run → 拉该 run 决策(时间正序),asof 映射 idx;窗外 offChart。
  useEffect(() => {
    let dead = false;
    if (!selRun) { setRunDecs([]); return; }
    (async () => {
      const rows = window.lzRunDecisions ? await window.lzRunDecisions(selRun.run_id) : [];
      if (dead) return;
      const isMin = (selRun.tf === '30min');
      const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : (symbol.bars || []);
      const byKey = {}; refBars.forEach((b, i) => { byKey[b.date] = i; });
      setRunDecs(rows.map(r => {
        const key = isMin ? String(r.asof || '').slice(0, 16) : String(r.asof || '').slice(0, 10);
        const idx = byKey[key] != null ? byKey[key] : -1;
        const dir = String(r.direction || '');
        const side = /买/.test(dir) ? 'buy' : (/卖/.test(dir) ? 'sell' : 'watch');
        return { key: 'run_' + (r.id || key), seat: r.strategy_id || 'run', idx, date: key, side,
                 direction: dir, conf: (r.confidence == null ? null : +r.confidence),
                  rationale: r.rationale || '', reasoning: r.reasoning || null, asof: r.asof,
                  model_name: r.model_name || '', key_evidence: r.key_evidence || [],
                  source: r.source || '', preset_id: r.preset_id || '', badges: r.badges || [],
                  triggers: r.triggers || [], escalation_triggers: r.escalation_triggers || [],
                  trigger_summary: r.trigger_summary || '', escalation_digest: r.escalation_digest || '',
                  node_summaries: r.node_summaries || null, agent_summaries: r.agent_summaries || null,
                  node_statuses: r.node_statuses || null, orchestration: r.orchestration || null,
                  deep_quality: r.deep_quality || null, evidence_warnings: r.evidence_warnings || null,
                  confidence_band: r.confidence_band || '',
                  orchestration_mode: r.orchestration_mode || '', orchestration_grade: r.orchestration_grade || '',
                  deep_variant: r.deep_variant || '', deep_status: r.deep_status || '', coverage: r.coverage || '',
                  evidence_note: r.evidence_note || '',
                 recipe_factors: r.recipe_factors || [], card_names: r.card_names || [],
                 factors_std: r.factors_std || null,
                 // 旧 run 只保留「曾引用」布尔，不把历史研报标题/正文带入详情对象。
                 legacy_research: !!r.legacy_research || (Array.isArray(r.research) && r.research.length > 0),
                 workspace_history_ref: r.workspace_history_ref || null,
                 regime_asof: r.regime_asof || null,
                 recipe_factors_vintage: r.recipe_factors_vintage || [],
                 hybrid_direction: r.hybrid_direction || dir,
                 factor_score: (r.factor_score == null ? null : r.factor_score),
                 hybrid_bias: (r.hybrid_bias == null ? null : r.hybrid_bias),
                 w: r.w || 0,
                 // 出场语义(2026-08-03):后端持仓语境下才有;复盘回测按 exit_fraction 部分了结,
                 //   研判卡按 position_action 显形。缺席即 null(空仓语境的行逐字同旧)。
                 position_action: r.position_action || null,
                 exit_fraction: (r.exit_fraction == null ? null : +r.exit_fraction),
                 exit_engine: r.exit_engine || null, t_ref: r.t_ref || null,
                 offChart: byKey[key] == null, _isRun: true };
      }));
    })();
    return () => { dead = true; };
  }, [selRun, symbol]);

  // 真日K接入(退避重试,后端恢复自动切真)
  useEffect(() => {
    if (realSyms[code]) { setDataMode('real'); return; }
    if (!window.lzFetchDailyBars) { setDataMode('mock'); return; }
    let alive = true, tries = 0, timer = null;
    const retry = () => { tries++; if (alive && tries <= 20) timer = setTimeout(attempt, Math.min(3000 * tries, 15000)); };
    const attempt = () => {
      if (!alive) return;
      window.lzFetchDailyBars(code, 250).then(async bars => {
        if (!alive) return;
        if (!bars) { setDataMode('mock'); retry(); return; }
        const benchRows = window.lzFetchBenchmark ? await window.lzFetchBenchmark(bars[0].date, bars[bars.length - 1].date) : null;
        const built = window.lzBuildSymbolFromBars(window.LZ_SYMBOLS[code].meta, bars, undefined, benchRows);
        built.benchRows = benchRows;
        setRealSyms(s => Object.assign({}, s, { [code]: built }));
        setDataMode('real');
      }).catch(() => { if (alive) { setDataMode('mock'); retry(); } });
    };
    attempt();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [code]);

  // ④-③ 官方日K落库自动接管(今日页,10min;有今日柱缓存才查)
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchDailyBars) return;
    let alive = true;
    const poll = () => {
      const lb = window.lzLivebarLoad ? window.lzLivebarLoad(code) : null;
      if (!lb) return;
      window.lzFetchDailyBars(code, 250).then(async bars => {
        if (!alive || !bars || !bars.length) return;
        const lastReal = bars[bars.length - 1].date;
        if (lastReal >= lb.date) {
          const benchRows = window.lzFetchBenchmark ? await window.lzFetchBenchmark(bars[0].date, bars[bars.length - 1].date) : null;
          const built = window.lzBuildSymbolFromBars(window.LZ_SYMBOLS[code].meta, bars, undefined, benchRows);
          built.benchRows = benchRows;
          setRealSyms(s => Object.assign({}, s, { [code]: built }));
          if (window.lzLivebarClear) window.lzLivebarClear(code);
          setLiveBar(null);
        }
      }).catch(() => {});
    };
    poll();
    const iv = setInterval(poll, 600000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, code]);

  // 切 TF / 标的 / 页 → 导航条复位
  useEffect(() => { setZoom(null); setPanEnd(null); }, [tf, code, page]);

  // 日内 TF / 30min run / 30min 向导 → 懒加载真 5min
  useEffect(() => {
    const need = (tf === '60' || tf === '30' || tf === '15' || tf === '5') || !!(selRun && selRun.tf === '30min') || (page === 'replay' && wizFreq === '30min');
    if (!need) return;
    const sym = realSyms[code];
    if (!sym || sym.bars5 || !window.lzFetchBars5) return;
    let alive = true;
    window.lzFetchBars5(code, 2400).then(bars5 => {
      if (!alive || !bars5) return;
      setRealSyms(s => {
        const cur = s[code];
        if (!cur || cur.bars5) return s;
        return Object.assign({}, s, { [code]: Object.assign({}, cur, { bars5 }) });
      });
    });
    return () => { alive = false; };
  }, [tf, code, realSyms, selRun, page, wizFreq]);

  // 今日页日内:盘中轮询今日 5min 拼接(30s)，休市只读一次最终快照。
  useEffect(() => {
    if (page !== 'today') return;
    if (tf !== '60' && tf !== '30' && tf !== '15' && tf !== '5') return;
    if (!window.lzFetchRealtimeBars5) return;
    let alive = true;
    const pull = () => window.lzFetchRealtimeBars5(code).then(live5 => {
      if (!alive || !live5 || !live5.length) return;
      setRealSyms(s => {
        const cur = s[code];
        if (!cur || !cur.bars5 || !cur.bars5.length) return s;
        // Same timestamp may be a forming 5min candle: replace by natural key,
        // do not append-only and leave the visible candle frozen.
        const byTime = new Map(cur.bars5.map(b => [b.date, b]));
        live5.forEach(b => byTime.set(b.date, b));
        const merged = Array.from(byTime.values()).sort((a, b) => String(a.date).localeCompare(String(b.date)));
        return Object.assign({}, s, { [code]: Object.assign({}, cur, { bars5: merged, bars5Meta: live5._meta || null }) });
      });
    });
    pull();
    if (liveMarketOpen !== true) return () => { alive = false; };
    const iv = setInterval(pull, 30000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, tf, code, liveMarketOpen]);

  // 今日页 1min:整批替换(20s)。packet.code + 单调 seq 双闸，防切票时旧请求晚回覆盖。
  useEffect(() => {
    if (page !== 'today' || tf !== '1' || !window.lzFetchRealtimeBars1) return;
    let alive = true; const requestCode = String(code).replace(/^(SH|SZ|BJ)/i, '');
    const pull = () => {
      const seq = ++bars1SeqRef.current;
      return window.lzFetchRealtimeBars1(requestCode, 480).then(packet => {
      if (!alive || seq !== bars1SeqRef.current || !packet || !packet.bars || !packet.bars.length) return;
      const responseCode = String(packet.code || packet.requestCode || '').replace(/^(SH|SZ|BJ)/i, '');
      if (responseCode !== requestCode) return;
      setRealSyms(s => { const cur = s[requestCode]; if (!cur) return s; return Object.assign({}, s, { [requestCode]: Object.assign({}, cur, { bars1: packet.bars, bars1Meta: { asof: packet.asof, fetchedAt: packet.fetchedAt, source: packet.source, seq } }) }); });
    });
    };
    pull();
    if (liveMarketOpen !== true) return () => { alive = false; bars1SeqRef.current++; };
    const iv = setInterval(pull, 20000);
    return () => { alive = false; bars1SeqRef.current++; clearInterval(iv); };
  }, [page, tf, code, liveMarketOpen]);

  // 复盘回灌:当前 run 成绩提炼为草稿经验卡
  const distillToCard = () => {
    if (!window.GL || !repPerf) return;
    const m = repPerf.metrics;
    const name = symbol.meta.name + ' · ' + curName + '复盘提炼';
    const id = GL.put({ type: 'card', title: name, cat: '实测', tags: ['复盘回灌', symbol.meta.industry],
      verdict: m.total > 0 ? '通过' : '存疑', conf: Math.max(40, Math.min(90, Math.round(52 + m.sharpe * 7))),
      insight: `${symbol.meta.name} 复盘(${frame.label}):${curName}累计 ${pct(m.total)}、Sharpe ${m.sharpe.toFixed(2)}、胜率 ${(m.winRate * 100).toFixed(0)}%。【复盘草稿·未经验证,不入信号】`,
      expr: '(实测落子序列)', status: 'draft', refs: [] });
    setToast({ id, name });
    setTimeout(() => setToast(null), 5200);
  };

  const frame = window.lzFrameData(symbol, tf, cursor, cursor, true, page === 'today');
  const intraday = tf === '60' || tf === '30' || tf === '15' || tf === '5' || tf === '1';
  const scaleReal = intraday ? !!frame.real5 : (dataMode === 'real');
  const bars1Meta = (realSyms[code] && realSyms[code].bars1Meta) || null;
  const bars5Meta = (realSyms[code] && realSyms[code].bars5Meta) || null;
  const bars1Asof = bars1Meta && bars1Meta.asof;
  const bars1Time = bars1Asof ? String(bars1Asof).slice(5, 16) : '';
  const bars1Ms = bars1Asof ? new Date(String(bars1Asof).replace(' ', 'T')).getTime() : NaN;
  const bars1Stale = tf === '1' && !!(watch && watch.marketOpen) && (!isFinite(bars1Ms) || (Date.now() - bars1Ms) > 180000);
  const bars5Asof = bars5Meta && bars5Meta.asof;
  const bars5Ms = bars5Asof ? new Date(String(bars5Asof).replace(' ', 'T')).getTime() : NaN;
  const bars5Stale = tf !== '1' && intraday && !!(watch && watch.marketOpen) && bars5Meta && (!isFinite(bars5Ms) || (Date.now() - bars5Ms) > 600000);
  const scaleLabel = intraday
    ? (frame.real5 ? (tf === '1' ? ((bars1Stale ? '1min·陈旧' : '真·1min') + (bars1Time ? ' · ' + bars1Time : '')) : (bars5Stale ? '5min·陈旧' : '真·5min')) : (dataMode === 'real' ? '日内·合成' : '样例'))
    : (dataMode === 'real' ? '真·日线' : '样例');
  const scaleTitle = intraday
    ? (frame.real5 ? (tf === '1' ? ('盘中实时 1min · 数据截至 ' + (bars1Asof || '未知') + (bars1Stale ? ' · 已超过3分钟未推进，标记陈旧' : '') + ' · /seats/bars_live?freq=1min') : ('结算历史 + 盘中实时 5min · 数据截至 ' + (bars5Asof || '历史末根') + (bars5Stale ? ' · 已超过10分钟未推进，标记陈旧' : '') + ' · 同步写入可复盘分钟证据库(/seats/bars_live?freq=5min)')) : '真 5min/1min 未到 / 窗口越界 → subdivideDay 合成,仅形态示意')
    : (dataMode === 'real' ? '日K来自 stock_data 真数据(/seats/daily)' : '真日K未到 / 后端未起 → 回退合成样例');
  // 今日页补今日柱(forming 实时 / 缓存快照;只入显示帧,绝不入 symbol.bars)
  const dispFrame = (() => {
    if (page !== 'today' || tf !== 'D') return frame;
    const last = symbol.bars[n - 1];
    if (!last) return frame;
    if (quote && Number.isFinite(+quote.price) && quote.asofDate && quote.asofDate > last.date) {
      const fo = +(quote.open != null ? quote.open : quote.price), fc = +quote.price;
      const fb = { i: frame.fbars.length, date: quote.asofDate, o: fo, c: fc,
        h: Math.max(quote.high != null ? +quote.high : -Infinity, fo, fc),
        l: Math.min(quote.low != null ? +quote.low : Infinity, fo, fc),
        v: +(quote.volume != null ? quote.volume : 0), forming: !!quote.fresh, today: true, event: false };
      return Object.assign({}, frame, { fbars: frame.fbars.concat([fb]), viewEnd: frame.viewEnd + 1 });
    }
    if (liveBar && liveBar.date > last.date) {
      const t = new Date(liveBar.date + 'T00:00:00').getTime();
      const today0 = new Date(); today0.setHours(0, 0, 0, 0);
      if (isFinite(t) && (today0.getTime() - t) <= 14 * 86400000) {
        const fb = { i: frame.fbars.length, date: liveBar.date, o: +liveBar.o, c: +liveBar.c,
          h: +liveBar.h, l: +liveBar.l, v: +liveBar.v || 0, forming: false, today: true, cached: true, event: false };
        return Object.assign({}, frame, { fbars: frame.fbars.concat([fb]), viewEnd: frame.viewEnd + 1 });
      }
    }
    return frame;
  })();
  // K线金框标记:复盘=选中 run(或进行中真跑);今日=后端落盘时间线
  const chartMarks = (() => {
    const src = page === 'replay' ? (selRun ? runDecs : (realDecs[code] || [])) : todayDecs;
    return window.lzMapDecsToFrame ? window.lzMapDecsToFrame(src, dispFrame.fbars) : [];
  })();
  // 可见窗口
  const maxEnd = dispFrame.viewEnd;
  const autoWin = page === 'today' ? (tf === 'D' ? 56 : tf === 'W' ? 22 : 60) : (maxEnd + 1);
  const vWin = Math.max(10, Math.min(zoom != null ? zoom : autoWin, maxEnd + 1));
  const vEnd = Math.max(vWin - 1, Math.min(panEnd != null ? panEnd : maxEnd, maxEnd));
  const chartView = { start: Math.max(0, vEnd - vWin + 1), end: vEnd };
  // 新闻:今日=live;复盘=PIT(asof=选中决策日 || run 终点 || 末根)
  const newsAsof = page === 'today' ? '' : ((selected && selected.date) || (selRun && (selRun.end_date || '')) || (symbol.bars[n - 1] || {}).date || '');
  useEffect(() => {
    if (!code) { setNewsPayload(null); return; }
    let alive = true;
    const t = setTimeout(() => {
      const nmode = page === 'today' ? 'live' : 'pit';
      window.lzFetchNews && window.lzFetchNews(code, page === 'today' ? '' : String(newsAsof).slice(0, 10), nmode).then(p => { if (alive) setNewsPayload(p); });
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [code, newsAsof, page]);
  const newsMarkers = useMemo(
    () => (window.lzMapNewsToFrame && newsPayload)
      ? window.lzMapNewsToFrame(newsPayload.items || [], dispFrame.fbars, newsKw) : [],
    [newsPayload, dispFrame, newsKw]);

  // 基准末个非 null 值
  let benchTotal = null;
  if (symbol.bench) {
    for (let bi = Math.min(n, symbol.bench.length) - 1; bi >= 0; bi--) {
      if (symbol.bench[bi] != null) { benchTotal = symbol.bench[bi] - 1; break; }
    }
  }
  const eqMin = page === 'replay' && selRun && selRun.tf === '30min' && repPerf;
  const eqLen = eqMin ? repPerf.eq.length : n;
  const eqReveal = eqMin ? repPerf.eq.length - 1 : cursor;
  const equityLines = [
    ...(repPerf ? [{ eq: repPerf.eq, color: 'var(--yin)', width: 2, fill: true, name: curName + ' · 纯LLM' }] : []),
    ...((repPerfHybrid && anyHybrid) ? [{ eq: repPerfHybrid.eq, color: 'var(--zhu)', width: 1.6, dash: '5 3', name: '混合(因子进信号)' }] : []),
    ...((symbol.bench && !eqMin) ? [{ eq: symbol.bench, color: 'var(--ink-3)', width: 1.2, dash: '4 3', dim: true, name: '基准' }] : []),
  ];
  const hybridDelta = (repPerf && repPerf.metrics && repPerfHybrid && repPerfHybrid.metrics)
    ? (repPerfHybrid.metrics.total - repPerf.metrics.total) : null;

  // ── 中栏(K线 + 头部;两页共用,差异经 props)──
  const chartCenter = (showEquity) => (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, borderRight: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 14px', borderBottom: '1px solid var(--line-soft)', flexShrink: 0 }}>
        <span className="serif" style={{ fontSize: 13, fontWeight: 600 }}>{symbol.meta.name}</span>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{code}</span>
        <span className="mono" title={scaleTitle}
          style={{ fontSize: 8.5, padding: '1px 6px', borderRadius: 4, whiteSpace: 'nowrap', border: '1px solid ' + (scaleReal ? 'var(--zhu-soft)' : 'var(--line)'), color: scaleReal ? 'var(--yin)' : 'var(--ink-3)' }}>
          {scaleLabel}
        </span>
        <TfPicker tf={tf} setTf={setTf} />
        <input value={newsKw} onChange={e => setNewsKw(e.target.value)}
          placeholder="新闻关键词 加息|非农|本票名"
          style={{ font: '11px var(--mono)', padding: '2px 7px', border: '1px solid var(--line)', borderRadius: 5, background: 'var(--paper)', color: 'var(--ink)', width: 150 }} />
        {newsPayload && newsPayload.coverage && newsPayload.coverage.partial &&
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>覆盖不全&lt;{newsPayload.coverage.floor}</span>}
        <span style={{ flex: 1 }} />
        {page === 'replay' && repPerf && <span onClick={distillToCard} className="mono" title="复盘回灌 · 提炼为新经验卡入共享库" style={{ fontSize: 10, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 6, padding: '3px 9px', cursor: 'pointer' }}>↺ 提炼为经验卡</span>}
        <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>朱砂涨 · 黛绿跌 · B 买 · S 卖</span>
      </div>
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <CandleChart bars={dispFrame.fbars} decisions={[]} truedecs={chartMarks} activeSeats={selRun ? Array.from(new Set(runDecs.map(d => d.seat))) : active} selected={selected} onSelect={setSelected} revealTo={dispFrame.freveal} view={chartView} live={page === 'today'} asOf={{ on: false, date: '' }} triggers={page === 'today' ? currentOrderTriggers : []} newsMarkers={newsMarkers} onNewsClick={setNewsPanel} />
        {newsPanel && (() => {
          const items = newsPanel.items || [];
          const sourceCN = {
            eastmoney_kuaixun: '东方财富 · 快讯', eastmoney: '东方财富',
            stock: '本票消息', macro: '宏观', policy: '政策', industry: '行业',
          };
          return (
            <section className="lz-news-panel" role="dialog" aria-label={'当日快讯，共 ' + (newsPanel.count || items.length) + ' 条'}>
              <header className="lz-news-panel__head">
                <div className="lz-news-panel__mark" aria-hidden="true">讯</div>
                <div>
                  <div className="lz-news-panel__eyebrow">MARKET PULSE</div>
                  <div className="lz-news-panel__title">当日快讯 <span>{newsPanel.count || items.length} 条</span></div>
                </div>
                <button className="lz-news-panel__close" type="button" aria-label="关闭快讯" title="关闭" onClick={() => setNewsPanel(null)}>×</button>
              </header>
              <div className="lz-news-panel__list">
                {!items.length && <div className="lz-news-panel__empty">当前时间点没有可展示的快讯</div>}
                {items.map((it, i) => {
                  const keywords = newsKw.split('|').map(s => s.trim()).filter(Boolean);
                  const hit = keywords.some(k => (it.title || '').indexOf(k) >= 0);
                  const stamp = String(it.ts || '').replace('T', ' ');
                  const sourceRaw = String(it.source || it.level || '快讯');
                  const source = sourceCN[sourceRaw] || sourceRaw.replace(/_/g, ' ');
                  return (
                    <article key={(it.ts || '') + ':' + (it.title || '') + ':' + i} className={'lz-news-item' + (hit ? ' is-hit' : '')}>
                      <time className="lz-news-item__time" dateTime={String(it.ts || '')}>
                        <b>{stamp.slice(11, 16) || '—'}</b>
                        <span>{stamp.slice(5, 10) || '—'}</span>
                      </time>
                      <div className="lz-news-item__body">
                        <div className="lz-news-item__meta">
                          <span className="lz-news-item__source" title={sourceRaw}>{source}</span>
                          {hit && <span className="lz-news-item__hit">关键词命中</span>}
                        </div>
                        <div className="lz-news-item__headline">{it.title || '未命名快讯'}</div>
                      </div>
                    </article>
                  );
                })}
              </div>
              <footer className="lz-news-panel__foot">按 K 线时间聚合 · 最新在前</footer>
            </section>
          );
        })()}
      </div>
      <ChartNav fbars={dispFrame.fbars} win={vWin} end={vEnd} maxEnd={maxEnd} setZoom={setZoom} setPanEnd={setPanEnd} />
      {showEquity && (
        <div style={{ height: 150, borderTop: '1px solid var(--line)', flexShrink: 0, position: 'relative' }}>
          <div style={{ position: 'absolute', top: 5, left: 10, zIndex: 2, display: 'flex', gap: 12, fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--ink-3)' }}>
            <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', fontWeight: 600 }}>收益曲线</span>
            {repPerf && <span><span style={{ color: 'var(--yin)' }}>━</span> {curName} · 纯LLM(run 真决策{runClock ? '+时钟止损止盈' : ''}模拟成交)</span>}
            {(repPerfHybrid && anyHybrid) && <span><span style={{ color: 'var(--zhu)' }}>┅</span> 混合(因子进信号){(repPerf && hybridDelta != null) ? ' · Δtotal=' + (hybridDelta >= 0 ? '+' : '') + (hybridDelta * 100).toFixed(2) + '%' : (!repPerf ? ' · 纯LLM零成交,仅混合线' : '')}</span>}
            {(repPerf && repPerfHybrid && !anyHybrid) && <span style={{ color: 'var(--ink-3)' }}>w=0 · 两线重合(未混入因子)</span>}
            {(!selRun && !repPerf) && <span style={{ color: 'var(--ink-3)' }}>未选回测 —— 上方向导跑一次,或右栏点开历史 run</span>}
            {(selRun && !repPerf && !(repPerfHybrid && anyHybrid)) && <span style={{ color: 'var(--ink-3)' }}>本 run 纯LLM全观望 · 零成交(空仓避险,无净值可画)</span>}
            {(symbol.bench && !eqMin) && <span><span style={{ color: 'var(--ink-3)' }}>┄</span> 基准</span>}
            {eqMin && <span style={{ color: 'var(--ink-3)' }}>30 分粒度 · 基准另案对齐</span>}
          </div>
          <EquityChart lines={equityLines} revealTo={eqReveal} len={eqLen} />
        </div>
      )}
    </div>
  );

  // ── 复盘向导条 ──
  const dayBarsAll = symbol.bars;
  const defStartShown = dayBarsAll[Math.max(0, dayBarsAll.length - (wizFreq === '30min' ? 10 : 120))].date;
  const defEndShown = dayBarsAll[dayBarsAll.length - 1].date;
  const wizardBar = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 16px', borderBottom: '1px solid var(--line)', background: 'rgba(168,57,45,0.03)', flexShrink: 0, flexWrap: 'wrap' }}>
      <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink-1)' }}>新建回测</span>
      <select value={code} onChange={e => setCode(e.target.value)} className="mono" style={{ fontSize: 10.5, padding: '3px 7px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)', cursor: 'pointer' }}>
        {window.LZ_SYMBOL_META.map(m => <option key={m.code} value={m.code}>{m.name} {m.code}</option>)}
      </select>
      <select value={curSid || ''} onChange={e => setCurStratId(e.target.value)} className="mono" style={{ fontSize: 10.5, padding: '3px 7px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)', cursor: 'pointer' }}>
        {availableStrategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        {!availableStrategies.length && <option value="">无策略 · 去「策略」页建</option>}
      </select>
      <input type="date" value={wizStart || defStartShown} onChange={e => setWizStart(e.target.value)} className="mono" title="起始日(默认近 120 交易日)" style={{ fontSize: 10.5, padding: '2px 6px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)' }} />
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>→</span>
      <input type="date" value={wizEnd || defEndShown} onChange={e => setWizEnd(e.target.value)} className="mono" title="结束日(默认最新)" style={{ fontSize: 10.5, padding: '2px 6px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)' }} />
      <select value={wizFreq} onChange={e => setWizFreq(e.target.value)} className="mono" title="研判粒度:日线=每交易日一判;30分钟=盘中逐 30min bar 判(需真 5min)" style={{ fontSize: 10.5, padding: '3px 7px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)', cursor: 'pointer' }}>
        <option value="day">日线</option>
        <option value="30min">30 分钟</option>
      </select>
      <span onClick={runRealThink} title="逐 bar 真调 LLM(PIT·只用≤当日信息);运行中再点=停;跑完自动选中该 run"
        className="serif" style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: realRun.stopping ? 'wait' : 'pointer', whiteSpace: 'nowrap', fontSize: 11.5, padding: '4px 13px', borderRadius: 7, flexShrink: 0, border: '1px solid ' + (realRun.running ? 'var(--dai)' : 'var(--zhu-soft)'), color: realRun.running ? 'var(--dai)' : 'var(--yin)', background: realRun.running ? 'rgba(74,107,92,0.07)' : 'rgba(168,57,45,0.05)' }}>
        {realRun.running
          ? <React.Fragment><span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--dai)', animation: 'pulse 1s ease-in-out infinite' }} />{realRun.stopping ? '正在收尾' : '停止'} · {realRun.done}/{realRun.total}{realRun.errors ? ' · ' + realRun.errors + '失败' : ''}</React.Fragment>
          : '✦ 开始真跑'}
      </span>
      <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>每 bar 一次 LLM 调用(fast),120 日 ≈ 数分钟;可中途停,已跑段照样成 run</span>
    </div>
  );

  return (
    <div className="paper-bg" style={{ width: '100%', height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: 'var(--sans)', color: 'var(--ink)' }}>
      <TopBar page={page} setPage={setPage} dark={dark} setDark={setDark}
        watch={watch} onToggleWatch={toggleWatch} assignmentMode={assignmentMode} />
      {page === 'strategy' ? (
        <Foundry />
      ) : page === 'today' ? (
        <React.Fragment>
          <MarketBar symbol={symbol} revealTo={cursor} mode="live" market={market} quote={quote} />
          <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
            <TicketList code={code} onSelect={setCode} poolTick={poolTickV} marketOpen={liveMarketOpen}
              assignments={watchAssignments} assignmentMode={assignmentMode}
              onConfigureWatch={setWatchEdit} />
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, position: 'relative' }}>
              <div style={{ display: 'flex', alignItems: 'stretch', borderBottom: '1px solid var(--line)', flexShrink: 0, overflowX: 'auto' }}>
                <StockHero symbol={symbol} rt={cursor} mode="live" quote={quote} />
                <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 14px' }}>
                  <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: 'var(--yin)', whiteSpace: 'nowrap' }}>{curName}</span>
                  <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginTop: 2, whiteSpace: 'nowrap' }}>当前策略 · 自动盯盘与本次研判同源</span>
                </div>
                <button type="button" onClick={() => setWatchEdit(symbol.meta)} className="mono" title="更换本票负责 Agent、周期、节奏与自动研判方式"
                  style={{ alignSelf: 'center', border: '1px solid var(--line)', borderRadius: 6, padding: '3px 8px', background: 'transparent', color: 'var(--ink-3)', fontSize: 8.5, cursor: 'pointer', whiteSpace: 'nowrap' }}>更换策略</button>
                <span style={{ flex: 1, minWidth: 10 }} />
                <button type="button" data-testid="luozi-workbench-trigger" aria-controls="luozi-workbench" aria-expanded={todayToolOpen}
                  onClick={() => toggleTodayTool(todayTool)} className="serif"
                  style={{ alignSelf: 'center', margin: '0 12px', display: 'flex', alignItems: 'center', gap: 7, border: '1px solid ' + (todayToolOpen ? 'var(--zhu-soft)' : 'var(--line)'), borderRadius: 8, padding: '5px 11px', background: todayToolOpen ? 'rgba(168,57,45,0.06)' : 'transparent', color: todayToolOpen ? 'var(--yin)' : 'var(--ink-2)', fontSize: 10.5, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                  {currentOrderTriggered && <span title="当前股票有条件单触发" style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--zhu)', boxShadow: '0 0 0 2px rgba(168,57,45,0.12)' }} />}
                  {todayToolOpen ? '收起操作台' : '打开操作台'} <span className="mono" style={{ fontSize: 8 }}>{todayToolOpen ? '×' : '◫'}</span>
                </button>
              </div>
              {chartCenter(false)}
              <aside id="luozi-workbench" data-testid="luozi-workbench" role="dialog" aria-modal="false" aria-label="落子操作台" aria-hidden={!todayToolOpen}
                style={{ position: 'absolute', zIndex: 24, top: 55, right: 0, bottom: 0, width: 438, maxWidth: 'calc(100% - 12px)', display: 'flex', flexDirection: 'column', background: 'var(--paper)', borderTop: '1px solid var(--line)', borderLeft: '1px solid var(--line)', boxShadow: todayToolOpen ? '-10px 0 30px rgba(28,24,20,0.18)' : 'none', transform: todayToolOpen ? 'translateX(0)' : 'translateX(calc(100% + 24px))', opacity: todayToolOpen ? 1 : 0, visibility: todayToolOpen ? 'visible' : 'hidden', pointerEvents: todayToolOpen ? 'auto' : 'none', transition: 'transform .2s ease, opacity .16s ease, visibility .2s' }}>
                <div style={{ flexShrink: 0, padding: '11px 13px 9px', borderBottom: '1px solid var(--line)', background: 'linear-gradient(180deg, rgba(138,111,63,0.055), transparent)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="seal serif" style={{ width: 24, height: 24, fontSize: 12 }}>落</span>
                    <div style={{ minWidth: 0 }}>
                      <div className="serif" style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--ink-1)' }}>{symbol.meta.name} · 操作台</div>
                      <div className="mono" style={{ marginTop: 2, fontSize: 8.5, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{symbol.meta.code} · {curName} · {quote && quote.fresh ? '实时盘中' : '最新快照'}</div>
                    </div>
                    <button type="button" onClick={() => setWatchEdit(symbol.meta)} className="mono" style={{ marginLeft: 'auto', border: '1px solid var(--line)', borderRadius: 6, padding: '3px 7px', background: 'transparent', color: 'var(--ink-3)', fontSize: 8.5, cursor: 'pointer', whiteSpace: 'nowrap' }}>更换策略</button>
                    <button type="button" aria-label="关闭操作台" onClick={() => setTodayToolOpen(false)} style={{ border: 'none', background: 'transparent', color: 'var(--ink-3)', fontSize: 17, cursor: 'pointer', padding: '0 2px' }}>×</button>
                  </div>
                  <div role="tablist" aria-label="操作台功能" style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 5, marginTop: 10 }}>
                    {workbenchTabs.map(([k, label]) => (
                      <button type="button" role="tab" key={k} aria-selected={todayTool === k} onClick={() => { setTodayTool(k); setTodayToolOpen(true); }}
                        style={{ border: '1px solid ' + (todayTool === k ? 'var(--zhu-soft)' : 'var(--line)'), borderRadius: 7, padding: '5px 8px', background: todayTool === k ? 'rgba(168,57,45,0.07)' : 'transparent', color: todayTool === k ? 'var(--yin)' : 'var(--ink-3)', fontFamily: 'var(--serif)', fontSize: 10.5, cursor: 'pointer' }}>{label}{k === 'order' && currentOrderTriggered ? ' · 已触发' : ''}</button>
                    ))}
                  </div>
                </div>
                <div style={{ flex: 1, minHeight: 0, position: 'relative', overflow: 'hidden' }}>
                  <div data-workbench-panel="judge" aria-hidden={todayTool !== 'judge'} style={{ display: todayTool === 'judge' ? 'block' : 'none', height: '100%', overflowY: 'auto' }}>
                    <JudgeCard code={symbol.meta.code} name={symbol.meta.name} industry={symbol.meta.industry || ''} strat={curStrat} regime={market && market.regime} quote={quote}
                      assignment={currentAssignment} assignmentMode={assignmentMode} onConfigureWatch={null}
                      onDecisionComplete={() => setTodayTrailBump(v => v + 1)} />
                  </div>
                  <div data-workbench-panel="order" aria-hidden={todayTool !== 'order'} style={{ display: todayTool === 'order' ? 'block' : 'none', height: '100%', overflowY: 'auto' }}>
                    <OrderWatchPanel code={symbol.meta.code} name={symbol.meta.name} mode="live" asOf={null} seatId={curSid} strategies={availableStrategies} symbol={symbol} marketOpen={liveMarketOpen}
                      onRecordChange={() => setTodayTrailBump(v => v + 1)}
                      onTrigger={(t) => setOrderTriggers(ts => [...ts.filter(x => x.id !== t.id), t])} />
                    <details style={{ margin: '10px 13px 14px', border: '1px solid var(--line)', borderRadius: 9, background: 'rgba(138,111,63,0.035)' }}>
                      <summary className="serif" style={{ cursor: 'pointer', padding: '8px 10px', color: 'var(--ink-2)', fontSize: 11 }}>行情参考 · 五档盘口 / 逐笔</summary>
                      <OrderbookTicksPanel book={book} ticks={ticks} />
                    </details>
                  </div>
                  <div data-workbench-panel="trail" aria-hidden={todayTool !== 'trail'} style={{ display: todayTool === 'trail' ? 'block' : 'none', height: '100%', overflowY: 'auto' }}>
                    <DecisionTrail code={symbol.meta.code} strategyId={curSid} active={todayToolOpen && todayTool === 'trail'} bump={todayTrailBump} />
                  </div>
                </div>
              </aside>
            </div>
          </div>
        </React.Fragment>
      ) : (
        <React.Fragment>
          <MarketBar symbol={symbol} revealTo={cursor} mode="backtest" market={market} quote={null} />
          {wizardBar}
          <MetricsStrip m={repPerf ? repPerf.metrics : ((repPerfHybrid && anyHybrid) ? repPerfHybrid.metrics : null)} benchTotal={benchTotal} label={'回测 · ' + curName + ((!repPerf && repPerfHybrid && anyHybrid) ? ' · 混合线' : '')} symbol={symbol} rt={cursor} mode="backtest" quote={null} ledger={null} />
          <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
            {chartCenter(true)}
            <div style={{ width: 372, flexShrink: 0, display: 'flex', flexDirection: 'column', minHeight: 0, overflowY: 'auto', overflowX: 'hidden', background: 'var(--paper)' }}>
              <RunPicker code={code} bump={runsBump} selRun={selRun} strategyId={curSid}
                strategyName={curName} runDecs={runDecs} selected={selected} onPickDec={setSelected}
                onSelect={(r) => { setSelRun(p => p && r && p.run_id === r.run_id ? null : r); setSelected(null); }} />
              <div style={{ flexShrink: 0, minHeight: 320 }}>
                {selected && selected._isRun
                  ? <RunDecCard dec={selected} />
                  : <div style={{ padding: 20, color: 'var(--ink-3)', fontFamily: 'var(--serif)', fontSize: 12.5, lineHeight: 1.7, textWrap: 'pretty' }}>
                      点上方 run 内的任意一笔决策,或 K 线上的金框 <b style={{ color: 'var(--zhu)' }}>B</b>/<b style={{ color: 'var(--dai)' }}>S</b> —— 此处摊开完整证据链:vintage 因子 IC、工作空间历史摘要使用记录、当日大盘、价量形态与思维链。
                    </div>}
              </div>
            </div>
          </div>
        </React.Fragment>
      )}
      {watchEdit && (
        <WatchAssignmentDialog meta={watchEdit}
          assignment={watchAssignments[String(watchEdit.code || '').replace(/^(SH|SZ|BJ)/i, '')] || null}
          assignmentMode={assignmentMode} strategies={window.lzStrategyList ? window.lzStrategyList() : availableStrategies}
          legacyEnabled={!!(window.lzPoolIsMonitored && window.lzPoolIsMonitored(watchEdit.code))}
          onClose={() => setWatchEdit(null)} onSave={saveWatchConfig} />
      )}
      {toast && (
        <div style={{ position: 'fixed', bottom: 70, left: '50%', transform: 'translateX(-50%)', zIndex: 60, display: 'flex', alignItems: 'center', gap: 11, background: 'var(--paper)', border: '1px solid var(--dai-soft)', borderRadius: 11, padding: '11px 16px', boxShadow: '0 6px 26px rgba(28,24,20,0.18)', animation: 'fadeIn .3s ease' }}>
          <span className="seal" style={{ width: 22, height: 22, fontSize: 12, background: 'var(--dai)' }}>瀾</span>
          <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)' }}>{toast.kind === 'handoff' ? toast.text : <React.Fragment>复盘已提炼为草稿经验卡「{toast.name}」· 入共享档案库</React.Fragment>}</span>
          {toast.kind !== 'handoff' && toast.name && <a href="../graph/观澜 · 研究图谱.html" className="mono" style={{ fontSize: 10.5, color: 'var(--yin)', textDecoration: 'none', borderBottom: '1px dashed var(--zhu-soft)' }}>看图谱 →</a>}
        </div>
      )}
    </div>
  );
}

// ───────── 顶栏(单轴三页)─────────
function TopBar({ page, setPage, dark, setDark, watch, onToggleWatch, assignmentMode }) {
  const tabs = [['today', '今日'], ['replay', '复盘'], ['strategy', '策略']];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '0 16px', height: 52, borderBottom: '1px solid var(--line)', background: 'rgba(241,234,217,0.6)', flexShrink: 0 }}>
      {!WW_EMBED && (<React.Fragment>
      <span className="serif" style={{ fontSize: 15, fontWeight: 600, letterSpacing: '.04em', whiteSpace: 'nowrap', flexShrink: 0 }}>落子</span>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 6px', whiteSpace: 'nowrap', flexShrink: 0 }}>交易决策 agent</span>
      <span style={{ color: 'var(--line)' }}>|</span>
      </React.Fragment>)}
      <div style={{ display: 'flex', border: '1px solid var(--line)', borderRadius: 8, overflow: 'hidden' }}>
        {tabs.map(([k, label]) => (
          <span key={k} onClick={() => setPage(k)} className="serif" style={{
            fontSize: 12.5, padding: '5px 16px', cursor: 'pointer',
            background: page === k ? 'var(--ink)' : 'transparent', color: page === k ? 'var(--paper)' : 'var(--ink-2)',
          }}>{label}</span>
        ))}
      </div>
      <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>
        {page === 'today' ? '看盘 · 研判 · 条件单 · 后端盯盘' : page === 'replay' ? '选区间一键真跑 · run 化历史' : '策略装配 · 配方 · 绑票'}
      </span>
      <span style={{ flex: 1 }} />
      {page === 'today' && <WatchControl watch={watch} onToggle={onToggleWatch} assignmentMode={assignmentMode} />}
      <span onClick={() => setDark(d => !d)} className="mono" title="宣纸 / 月夜"
        style={{ fontSize: 11, padding: '4px 11px', border: '1px solid var(--line)', borderRadius: 7, color: 'var(--ink-2)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
        {dark ? '◐ 月夜' : '◑ 宣纸'}
      </span>
    </div>
  );
}

// ───────── 时间尺度选择 ─────────
function TfPicker({ tf, setTf }) {
  const opts = [['W', '周'], ['D', '日'], ['60', '60分'], ['30', '30分'], ['15', '15分'], ['5', '5分'], ['1', '1分']];
  return (
    <div title="图表周期只改变K线显示，不改变 Agent 研判数据周期、自动节奏或条件单指标口径" style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
      <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', whiteSpace: 'nowrap', marginRight: 1 }}>图表周期·仅显示</span>
      {opts.map(([k, l]) => (
        <span key={k} onClick={() => setTf(k)} className="mono" style={{ fontSize: 10, padding: '2px 7px', borderRadius: 5, cursor: 'pointer', border: '1px solid ' + (tf === k ? 'var(--ink)' : 'var(--line)'), background: tf === k ? 'var(--ink)' : 'transparent', color: tf === k ? 'var(--paper)' : 'var(--ink-3)' }}>{l}</span>
      ))}
    </div>
  );
}

// ───────── 图表导航条 (尺度缩放 + 时间轴平移) ─────────
function ChartNav({ fbars, win, end, maxEnd, setZoom, setPanEnd }) {
  const total = maxEnd + 1;
  const canPan = win <= maxEnd;
  const start = Math.max(0, end - win + 1);
  const dOf = (i) => { const b = fbars[Math.max(0, Math.min(i, fbars.length - 1))]; return b ? (b.date || '').slice(5) : ''; };
  const btn = { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 16, fontSize: 11, cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--ink-2)', userSelect: 'none' };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 14px', borderTop: '1px solid var(--line-soft)', background: 'rgba(28,24,20,0.015)', flexShrink: 0 }}>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>尺度</span>
      <span onClick={() => setZoom(Math.max(10, Math.round(win * 0.6)))} title="放大 · 更少 K" style={btn}>−</span>
      <input type="range" min={10} max={total} value={win}
        onChange={e => { const w = +e.target.value; setZoom(w >= total ? null : w); }}
        title="可见 K 线数(尺度)" style={{ width: 100, accentColor: 'var(--yin)', cursor: 'pointer' }} />
      <span onClick={() => setZoom(Math.min(total, Math.round(win * 1.6)))} title="缩小 · 更多 K" style={btn}>+</span>
      <span onClick={() => { setZoom(null); setPanEnd(null); }} title="复位 · 显示全部" style={{ ...btn, width: 'auto', padding: '0 6px', fontSize: 10 }}>全</span>
      <span style={{ color: 'var(--line)', fontSize: 11 }}>|</span>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>滑动</span>
      <input type="range" min={Math.max(0, win - 1)} max={maxEnd} value={end} disabled={!canPan}
        onChange={e => { const v = +e.target.value; setPanEnd(v >= maxEnd ? null : v); }}
        title="沿时间轴平移" style={{ flex: 1, accentColor: 'var(--yin)', cursor: canPan ? 'pointer' : 'default', opacity: canPan ? 1 : 0.35 }} />
      <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', whiteSpace: 'nowrap', minWidth: 124, textAlign: 'right' }}>
        {dOf(start)}–{dOf(end)} · {win}/{total} 根
      </span>
    </div>
  );
}
