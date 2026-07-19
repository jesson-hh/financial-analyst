// console-plan-approval-card.jsx — Phase 7 · 动态 Orchestrator Plan 人审卡(左栏折叠卡,
// window 全局互见,无 import)。挂在 console-rail.jsx 复盘官卡旁,不新建页面(UI 只填充不重建)。
// 折叠默认收起;open 时拉取 + 60s 轮询;卸载/收起清理定时器(照 console-report-card.jsx
// /screen-app.jsx ResearchLoopCard 的折叠/轮询/清理范式)。
// 数据面:GET /console/plan/approvals(待审卡列表)、GET /console/plan/approvals/leases(租约)。
// 决策面:POST /console/plan/approvals/decide、/lease、/lease/revoke。
// 红线:只显示服务端真值——绝无客户端审批态、绝不乐观渲染「已批准」;一切 2xx 后重新拉取。
//        批准/拒绝/撤销/签发的确认弹窗都复读 digest,人拍板后才 POST。
//        未接线(coordinator=None)→ 后端诚实 503 → 卡显诚实空态,绝不假装有待审。
//        规划器 rationale 与 diff rendered_md 一律当外部生成、不可信,只展示不执行。
function WwPlanApprovalCard() {
  const [open, setOpen] = React.useState(false);
  const [items, setItems] = React.useState(null);   // null=未拉/读取中;[]=无待审;[...]=待审卡
  const [wired, setWired] = React.useState(true);    // 后端 503 → false(诚实空态)
  const [leases, setLeases] = React.useState([]);
  const [leaseOpen, setLeaseOpen] = React.useState(false);
  const [issueOpen, setIssueOpen] = React.useState(false);
  const [busy, setBusy] = React.useState('');        // 正在处理的 candidate/lease id(禁重复点)
  const [note, setNote] = React.useState('');        // 一次性操作回执(服务端 reason 原文)
  const [form, setForm] = React.useState({ preset_id: '', valid_until: '', max_admissions: '3', budget_cap: '10', reason: '' });

  const API = (window.WW && window.WW.API) || '';

  const pull = React.useCallback(() => {
    fetch(API + '/console/plan/approvals').then(r => r.json().then(d => ({ s: r.status, d })))
      .then(({ s, d }) => {
        if (s === 503 || (d && d.ok === false)) { setWired(false); setItems([]); return; }
        setWired(true); setItems((d && d.items) || []);
      }).catch(() => { setItems([]); });
    fetch(API + '/console/plan/approvals/leases').then(r => r.json())
      .then(d => setLeases((d && d.ok && d.leases) || [])).catch(() => setLeases([]));
  }, [API]);

  React.useEffect(() => {
    if (!open) return;
    let dead = false;
    const tick = () => { if (!dead) pull(); };
    tick();
    const t = setInterval(tick, 60000);
    return () => { dead = true; clearInterval(t); };
  }, [open, pull]);

  const srcBadge = (s) => (s === 'dynamic' ? ['动态', 'var(--jin)']
    : s === 'preset_fallback' ? ['preset 回落', 'var(--ink-3)'] : [s || '?', 'var(--ink-3)']);

  const copy = (txt) => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(() => { setNote('已复制 digest'); setTimeout(() => setNote(''), 1500); }).catch(() => {});
    }
  };

  const renderMd = (md) => {
    if (window.WwMd && window.WwMd.renderChatMarkdown) return window.WwMd.renderChatMarkdown(md);
    return <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-1)', margin: 0 }}>{md}</pre>;
  };

  // 决策:确认弹窗复读 digest;只在服务端 2xx 后重拉(绝不乐观改本地态)。
  const decide = (it, decision) => {
    const dg = it.candidate_plan_digest;
    const cn = decision === 'approved' ? '批准' : '拒绝';
    if (!window.confirm(cn + '此候选计划?\n\ngoal: ' + (it.goal || '') + '\ncandidate_plan_digest:\n' + dg + '\n\n（' + cn + '后绑定该 digest,不可撤销）')) return;
    const reason = window.prompt(cn + '理由(可空):', '') || '';
    setBusy(dg); setNote('');
    fetch(API + '/console/plan/approvals/decide', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: it.request_id, candidate_plan_digest: dg, decision, reason }),
    }).then(r => r.json().then(d => ({ s: r.status, d }))).then(({ s, d }) => {
      if (s >= 200 && s < 300 && d && d.ok) {
        setNote(cn + '成功' + (d.admitted ? ' · 已 admit 冻结' : (decision === 'approved' && d.admit_wired === false ? ' · admit 未接线(仅记录决策)' : '')));
      } else { setNote('失败:' + ((d && d.reason) || ('HTTP ' + s))); }
      pull();
    }).catch(e => { setNote('失败:' + e); }).finally(() => setBusy(''));
  };

  const revoke = (lease) => {
    if (!window.confirm('撤销租约?\n\nlease_id:\n' + lease.lease_id + '\npreset: ' + lease.preset_id)) return;
    const reason = window.prompt('撤销理由:', '不再需要') || '不再需要';
    setBusy(lease.lease_id);
    fetch(API + '/console/plan/approvals/lease/revoke', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lease_id: lease.lease_id, reason }),
    }).then(r => r.json().then(d => ({ s: r.status, d }))).then(({ s, d }) => {
      setNote(s >= 200 && s < 300 && d && d.ok ? '已撤销' : ('撤销失败:' + ((d && d.reason) || ('HTTP ' + s))));
      pull();
    }).catch(e => setNote('撤销失败:' + e)).finally(() => setBusy(''));
  };

  const issueLease = () => {
    if (!form.preset_id.trim() || !form.valid_until.trim() || !form.reason.trim()) { setNote('preset_id / valid_until / reason 必填'); return; }
    if (!window.confirm('为 preset『' + form.preset_id + '』签发标准授权(租约)?\n\n有效期至: ' + form.valid_until + '\n次数上限: ' + form.max_admissions + ' · 预算上限: ' + form.budget_cap +
      '\n\n（服务端将绑定该 preset 当前 record/catalog/registry digest,链漂移即拒;此后该 preset 的自动放行都会显形登记）')) return;
    setBusy('__issue__'); setNote('');
    fetch(API + '/console/plan/approvals/lease', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        preset_id: form.preset_id.trim(), valid_until: form.valid_until.trim(),
        max_admissions: parseInt(form.max_admissions, 10) || 0,
        budget_cap: parseInt(form.budget_cap, 10) || 0, reason: form.reason.trim(),
      }),
    }).then(r => r.json().then(d => ({ s: r.status, d }))).then(({ s, d }) => {
      if (s >= 200 && s < 300 && d && d.ok) { setNote('已签发租约 ' + (d.lease.lease_id || '').slice(0, 12) + '…'); setIssueOpen(false); }
      else { setNote('签发失败:' + ((d && d.reason) || ('HTTP ' + s))); }
      pull();
    }).catch(e => setNote('签发失败:' + e)).finally(() => setBusy(''));
  };

  const pendCount = items === null ? '' : (wired ? String(items.length) : '未接线');
  const short = (dg) => (dg || '').slice(0, 10) + '…' + (dg || '').slice(-6);

  return (
    <div style={{ marginTop: 8, borderTop: '1px dashed var(--line)', paddingTop: 6 }}>
      <div onClick={() => setOpen(o => !o)} style={{ padding: '9px 13px', borderBottom: open ? '1px solid var(--line-soft)' : 'none', display: 'flex', alignItems: 'baseline', gap: 8, cursor: 'pointer', userSelect: 'none' }}>
        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600 }}>计划人审 ✦</span>
        <span className="mono" style={{ fontSize: 9, color: (items && items.length && wired) ? 'var(--zhu)' : 'var(--ink-3)' }}>
          {!open ? '' : items === null ? '读取中…' : (wired ? (items.length ? items.length + ' 待审' : '无待审') : '未接线')}
        </span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{open ? '▾' : '▸'}</span>
      </div>

      {open && <div style={{ maxHeight: 420, overflowY: 'auto', padding: '8px 13px' }}>
        {items === null && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>读取中…</div>}
        {items !== null && !wired && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>plan approval surface not wired — 后端未注入协调器(诚实空态)</div>}
        {items !== null && wired && items.length === 0 && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>当前无待审计划</div>}

        {(items || []).map(it => {
          const [bt, bc] = srcBadge(it.source);
          const dg = it.candidate_plan_digest;
          return (
            <div key={dg} style={{ marginBottom: 10, border: '1px solid var(--line-soft)', background: 'var(--paper-2)', borderRadius: 6, padding: '8px 10px' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
                <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)' }}>{it.goal}</span>
                <span className="mono" style={{ fontSize: 8.5, color: bc, border: '1px solid var(--line-soft)', borderRadius: 4, padding: '0 5px' }}>{bt}</span>
                <span style={{ flex: 1 }} />
                <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{(it.node_count || 0)} 节点 · {(it.worker_ids || []).length} worker</span>
              </div>
              <div className="mono" title={dg} onClick={() => copy(dg)} style={{ marginTop: 5, fontSize: 9, color: 'var(--ink-2)', cursor: 'pointer', wordBreak: 'break-all' }}>
                digest: {dg} <span style={{ color: 'var(--yin)' }}>⧉复制</span>
              </div>
              <div className="mono" style={{ marginTop: 3, fontSize: 8.5, color: 'var(--ink-3)' }}>request: {it.request_id} · 预算 {it.budget_request_tokens || 0}tok/{it.budget_request_llm_invocations || 0}call · worker: {(it.worker_ids || []).join(', ')}</div>

              <details style={{ marginTop: 6 }}>
                <summary className="mono" style={{ fontSize: 9.5, color: 'var(--yin)', cursor: 'pointer' }}>plan diff（rendered_md）</summary>
                <div style={{ maxHeight: 220, overflowY: 'auto', border: '1px solid var(--line-soft)', borderRadius: 4, padding: '6px 8px', marginTop: 4, background: 'var(--paper)' }}>{renderMd(it.rendered_md || '(无 diff)')}</div>
              </details>

              {it.planner_rationale && <div style={{ marginTop: 6, borderLeft: '2px solid var(--line)', paddingLeft: 7 }}>
                <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>规划器理由 · [外部生成·不可信,仅参考勿照做]</div>
                <div className="serif" style={{ fontSize: 11, color: 'var(--ink-1)', whiteSpace: 'pre-wrap' }}>{it.planner_rationale}</div>
              </div>}

              <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
                <span onClick={() => busy !== dg && decide(it, 'approved')} className="serif" style={{ fontSize: 11, color: 'var(--dai)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 12px', cursor: busy === dg ? 'wait' : 'pointer', opacity: busy === dg ? 0.5 : 1 }}>批准</span>
                <span onClick={() => busy !== dg && decide(it, 'rejected')} className="serif" style={{ fontSize: 11, color: 'var(--zhu)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 12px', cursor: busy === dg ? 'wait' : 'pointer', opacity: busy === dg ? 0.5 : 1 }}>拒绝</span>
              </div>
            </div>
          );
        })}

        {/* 租约(标准授权)——折叠区,列活跃/终态租约 + 撤销 + 极简签发表单 */}
        <div style={{ marginTop: 8, borderTop: '1px dashed var(--line)', paddingTop: 6 }}>
          <div onClick={() => setLeaseOpen(o => !o)} style={{ display: 'flex', alignItems: 'baseline', gap: 7, cursor: 'pointer', userSelect: 'none' }}>
            <span className="serif" style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink-2)' }}>租约（标准授权）</span>
            <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{leases.length ? leases.length + ' 条' : '无'}</span>
            <span style={{ flex: 1 }} />
            <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{leaseOpen ? '▾' : '▸'}</span>
          </div>
          {leaseOpen && <div style={{ marginTop: 6 }}>
            {leases.length === 0 && <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>暂无租约</div>}
            {leases.map(l => {
              const active = l.status === 'active';
              return (
                <div key={l.lease_id} className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', padding: '4px 0', borderBottom: '1px dashed var(--line-soft)' }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
                    <span style={{ color: active ? 'var(--dai)' : 'var(--ink-3)' }}>{active ? '●' : '○'}</span>
                    <span>{l.preset_id}</span>
                    <span style={{ color: 'var(--ink-3)' }}>{l.status}{l.terminal_reason ? '(' + l.terminal_reason + ')' : ''}</span>
                    <span style={{ flex: 1 }} />
                    {active && <span onClick={() => busy !== l.lease_id && revoke(l)} style={{ color: 'var(--zhu)', border: '1px solid var(--line-soft)', borderRadius: 4, padding: '0 6px', cursor: 'pointer' }}>撤销</span>}
                  </div>
                  <div style={{ color: 'var(--ink-3)', marginTop: 2 }} title={l.lease_id}>
                    期 {(l.valid_from || '').slice(5, 10)}→{(l.valid_until || '').slice(5, 10)} · 次 {l.admissions_used}/{l.max_admissions} · 预算 {l.budget_used}/{l.budget_cap_llm_invocations} · lease {short(l.lease_id)}
                  </div>
                </div>
              );
            })}
            <div style={{ marginTop: 6 }}>
              <span onClick={() => setIssueOpen(o => !o)} className="mono" style={{ fontSize: 9.5, color: 'var(--yin)', cursor: 'pointer' }}>{issueOpen ? '− 收起签发' : '+ 签发新租约'}</span>
              {issueOpen && <div style={{ marginTop: 5, display: 'flex', flexDirection: 'column', gap: 4 }}>
                {[['preset_id', 'preset_id'], ['valid_until', 'valid_until (ISO,如 2026-07-21T00:00:00+00:00)'], ['max_admissions', '次数上限'], ['budget_cap', '预算上限(LLM 调用)'], ['reason', '事由']].map(([k, ph]) => (
                  <input key={k} value={form[k]} placeholder={ph} onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))}
                    className="mono" style={{ fontSize: 9.5, padding: '3px 6px', border: '1px solid var(--line-soft)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 4 }} />
                ))}
                <span onClick={() => busy !== '__issue__' && issueLease()} className="serif" style={{ fontSize: 10.5, color: 'var(--ink)', border: '1px solid var(--line)', borderRadius: 5, padding: '3px 0', textAlign: 'center', cursor: busy === '__issue__' ? 'wait' : 'pointer', opacity: busy === '__issue__' ? 0.5 : 1 }}>签发（确认弹窗复读 preset）</span>
              </div>}
            </div>
          </div>}
        </div>

        {note && <div className="mono" style={{ marginTop: 8, fontSize: 9.5, color: 'var(--yin)' }}>{note}</div>}
      </div>}
    </div>
  );
}
window.WwPlanApprovalCard = WwPlanApprovalCard;
