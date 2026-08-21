// console-report-card.jsx — 复盘日报顶栏入口(window 全局互见,无 import)。
// GET /autonomy/report/latest(latest.md+json)+ GET /autonomy/jobs?limit=8(job 历史)。
// 挂载先拉一次,让收起态也能显报告日期/陈旧提示;open 时 60s 轮询,收起/卸载清理。
// 只按 ok 分支渲染(绝不字符串匹配 reason);ok:false → 显示后端 reason 原文的诚实空态。
// window.WwMd 渲染 md(console-thread.jsx 定义;该文件脚本顺序在本文件之后加载,
// 故只能在渲染时惰性引用 window.WwMd,不可在顶层解构——WwMd 不存在时 <pre> 兜底)。
// 蒸馏草稿(duties.distill_draft 非空)只提供「复制蒸馏指令」按钮——navigator.clipboard 写入
// 剪贴板,人到对话框粘贴发送才真正调用 ww_rerank_distill(该工具本身 confirm:true);
// 本卡绝不自动发送,人审门不绕过。
function WwReviewReportCard() {
  const [open, setOpen] = React.useState(false);
  const [rep, setRep] = React.useState(null);      // null=未拉/读取中,{ok:false,reason}=无报告,{ok:true,date,md,json}=已拉到
  const [jobs, setJobs] = React.useState([]);
  const [copied, setCopied] = React.useState(false);
  const API = (window.WW && window.WW.API) || '';

  const pull = React.useCallback(() => {
    fetch(API + '/autonomy/report/latest').then(r => r.json())
      .then(d => setRep(d)).catch(() => setRep({ ok: false, reason: '后端不可达' }));
    fetch(API + '/autonomy/jobs?limit=8').then(r => r.json())
      .then(d => setJobs((d && d.jobs) || [])).catch(() => setJobs([]));
  }, [API]);

  React.useEffect(() => { pull(); }, [pull]);

  React.useEffect(() => {
    if (!open) return;
    pull();
    const t = setInterval(pull, 60000);
    return () => clearInterval(t);
  }, [open, pull]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const SC = { done: ['✓', 'var(--dai)'], failed: ['✗', 'var(--zhu)'], running: ['⟳', 'var(--jin)'], interrupted: ['⚠', 'var(--ink-3)'] };
  const fmtTs = (s) => (s || '').slice(5, 16).replace('T', ' ');   // 月-日 时:分,与会话行同口径

  // 草稿正文常见形如「(行业·XX) 正文…」——抓括号内「行业·」段作 key;
  // 抓不到就用草稿前 12 字兜底(ww_rerank_distill 服务端本就会强制补「行业·」前缀,兜底不影响调用)。
  const distillKey = (draft) => {
    const m = (draft || '').match(/[(（]\s*(行业[·\-][^)）]{1,20}?)\s*[)）]/);
    if (m) return m[1].trim();
    return '行业·' + (draft || '').slice(0, 12).trim();
  };
  const copyDistill = (draft) => {
    const key = distillKey(draft);
    const text = '用 ww_rerank_distill 蒸馏:key=' + key + ' text=' + (draft || '');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        setCopied(true); setTimeout(() => setCopied(false), 1800);
      }).catch(() => {});
    }
  };

  const renderMd = (md) => {
    if (window.WwMd && window.WwMd.renderChatMarkdown) return window.WwMd.renderChatMarkdown(md);
    return <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'var(--serif)', fontSize: 12.5, color: 'var(--ink)', margin: 0 }}>{md}</pre>;
  };

  const dd = (rep && rep.ok && rep.json && rep.json.duties) ? rep.json.duties.distill_draft : null;
  const reportDate = (rep && rep.ok && rep.date) ? String(rep.date).slice(0, 10) : '';
  const reportAgeDays = (() => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(reportDate)) return null;
    const p = reportDate.split('-').map(Number);
    const now = new Date();
    const todayUtc = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    const reportUtc = Date.UTC(p[0], p[1] - 1, p[2]);
    const days = Math.floor((todayUtc - reportUtc) / 86400000);
    return days >= 0 ? days : null;
  })();
  const stale = reportAgeDays !== null && reportAgeDays > 0;
  const headline = rep === null ? '读取中…' : (rep.ok ? (reportDate || '日期未知') : '暂无');
  const staleLabel = stale ? ('⚠ 陈旧 ' + reportAgeDays + ' 天') : '';
  const freshnessLabel = stale ? ('⚠ 距今 ' + reportAgeDays + ' 个自然日，请注意时效')
    : (reportAgeDays === 0 ? '今日最新' : '时效未校验');

  return (
    <div style={{ position: 'relative', display: 'inline-flex', flexShrink: 0 }}>
      <button type="button" aria-expanded={open} aria-haspopup="dialog"
        title={stale ? ('最新可用日报为 ' + reportDate + '，距今 ' + reportAgeDays + ' 天') : '查看盘后复盘日报、任务与蒸馏草稿'}
        onClick={() => setOpen(o => !o)}
        style={{ height: 24, padding: '0 9px', border: '1px solid ' + (stale ? 'var(--jin)' : (open ? 'var(--yin)' : 'var(--line)')), borderRadius: 12,
          display: 'inline-flex', alignItems: 'center', gap: 6, background: open ? 'var(--paper-2)' : 'var(--paper)', color: 'var(--ink)',
          cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap', fontFamily: 'var(--serif)' }}>
        <span style={{ fontSize: 11.5, fontWeight: 600 }}>复盘日报</span>
        <span className="mono" style={{ fontSize: 8.5, color: stale ? 'var(--jin)' : 'var(--ink-3)' }}>{headline}</span>
        {stale && <span className="mono" style={{ fontSize: 8, color: 'var(--jin)' }}>旧</span>}
        <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{open ? '▴' : '▾'}</span>
      </button>

      {open && <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 54 }} />}
      {open && <div role="dialog" aria-label="复盘日报" style={{ position: 'absolute', top: 31, right: 0, zIndex: 55,
        width: 540, maxWidth: 'calc(100vw - 32px)', background: 'var(--paper)', border: '1px solid var(--ink)', borderRadius: 7,
        boxShadow: '0 16px 48px rgba(28,24,20,.2)', overflow: 'hidden', textAlign: 'left' }}>
        <div style={{ minHeight: 38, padding: '0 13px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 8, background: 'var(--paper-2)' }}>
          <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>盘后复盘官 · 只读日报</span>
          <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{headline}</span>
          {stale && <span className="mono" title={'报告日期 ' + reportDate + '，距今 ' + reportAgeDays + ' 个自然日'}
            style={{ fontSize: 8.5, color: 'var(--jin)', border: '1px solid var(--jin)', borderRadius: 4, padding: '1px 5px' }}>{staleLabel}</span>}
          <span style={{ flex: 1 }} />
          <span onClick={() => setOpen(false)} title="关闭" className="mono"
            style={{ fontSize: 13, color: 'var(--ink-3)', cursor: 'pointer', userSelect: 'none', padding: '4px' }}>×</span>
        </div>

        <div style={{ maxHeight: 'min(68vh, 560px)', overflowY: 'auto', padding: '11px 14px 14px' }}>
          {rep === null && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>读取中…</div>}
          {rep !== null && !rep.ok && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{rep.reason || '暂无日报'}</div>}
          {rep !== null && rep.ok && <div>
            <div className="mono" style={{ fontSize: 9, color: stale ? 'var(--jin)' : 'var(--ink-3)', marginBottom: 8 }}>
              报告日期 {reportDate || '未知'} · {freshnessLabel}
            </div>
            <div className="serif">{renderMd(rep.md)}</div>
          </div>}

          {dd && <div style={{ marginTop: 12, borderTop: '1px dashed var(--line)', paddingTop: 9 }}>
            <div className="serif" style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>蒸馏草稿(待人审 · 未入记忆)</div>
            <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>{dd.draft}</div>
            <div style={{ marginTop: 7, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span onClick={(e) => { e.stopPropagation(); copyDistill(dd.draft); }} className="serif" style={{ fontSize: 10.5, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 5, padding: '2px 8px', cursor: 'pointer', userSelect: 'none' }}>复制蒸馏指令</span>
              <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{dd.note}</span>
              {copied && <span className="mono" style={{ fontSize: 9, color: 'var(--dai)' }}>已复制 — 粘贴到对话框发送(人审门不绕过)</span>}
            </div>
          </div>}

          <div style={{ marginTop: 12, borderTop: '1px dashed var(--line)', paddingTop: 8 }}>
            <div className="serif" style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 4 }}>最近任务</div>
            {jobs.length === 0 && <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>暂无任务记录</div>}
            {jobs.map(j => {
              const sc = SC[j.status] || ['·', 'var(--ink-3)'];
              return (
                <div key={j.job_id} className="mono" title={j.error || ''} style={{ fontSize: 9.5, color: 'var(--ink-2)', display: 'flex', gap: 7, alignItems: 'baseline', padding: '3px 0' }}>
                  <span style={{ color: sc[1], flexShrink: 0 }}>{sc[0]}</span>
                  <span style={{ flexShrink: 0 }}>{j.playbook || '?'}</span>
                  <span style={{ color: 'var(--ink-3)' }}>{fmtTs(j.started_ts)}</span>
                  <span style={{ flex: 1 }} />
                  <span style={{ color: 'var(--ink-3)' }}>{j.status}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>}
    </div>
  );
}
window.WwReviewReportCard = WwReviewReportCard;
