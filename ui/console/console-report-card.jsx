// console-report-card.jsx — Task 7:复盘官晨报卡(左栏折叠卡,window 全局互见,无 import)。
// GET /autonomy/report/latest(latest.md+json)+ GET /autonomy/jobs?limit=8(job 历史)。
// 折叠默认收起;open 时拉取+60s 轮询;卸载/收起清理定时器(照 screen-app.jsx
// ResearchLoopCard :991-1027 的折叠/轮询/清理/状态字形范式)。
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

  React.useEffect(() => {
    if (!open) return;
    let dead = false;
    const pull = () => {
      fetch(window.WW.API + '/autonomy/report/latest').then(r => r.json())
        .then(d => { if (!dead) setRep(d); }).catch(() => { if (!dead) setRep({ ok: false, reason: '后端不可达' }); });
      fetch(window.WW.API + '/autonomy/jobs?limit=8').then(r => r.json())
        .then(d => { if (!dead) setJobs((d && d.jobs) || []); }).catch(() => { if (!dead) setJobs([]); });
    };
    pull();
    const t = setInterval(pull, 60000);
    return () => { dead = true; clearInterval(t); };
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

  return (
    <div style={{ marginTop: 8, borderTop: '1px dashed var(--line)', paddingTop: 6 }}>
      <div onClick={() => setOpen(o => !o)} style={{ padding: '9px 13px', borderBottom: open ? '1px solid var(--line-soft)' : 'none', display: 'flex', alignItems: 'baseline', gap: 8, cursor: 'pointer', userSelect: 'none' }}>
        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600 }}>盘后复盘官 ✦</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>
          {!open ? '' : rep === null ? '读取中…' : (rep.ok ? rep.date : '暂无日报')}
        </span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{open ? '▾' : '▸'}</span>
      </div>
      {open && <div style={{ maxHeight: 360, overflowY: 'auto', padding: '8px 13px' }}>
        {rep === null && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>读取中…</div>}
        {rep !== null && !rep.ok && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{rep.reason || '暂无日报'}</div>}
        {rep !== null && rep.ok && <div className="serif">{renderMd(rep.md)}</div>}

        {dd && <div style={{ marginTop: 10, borderTop: '1px dashed var(--line)', paddingTop: 8 }}>
          <div className="serif" style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>蒸馏草稿(待人审 · 未入记忆)</div>
          <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>{dd.draft}</div>
          <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span onClick={(e) => { e.stopPropagation(); copyDistill(dd.draft); }} className="serif" style={{ fontSize: 10.5, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 5, padding: '2px 8px', cursor: 'pointer' }}>复制蒸馏指令</span>
            <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{dd.note}</span>
            {copied && <span className="mono" style={{ fontSize: 9, color: 'var(--dai)' }}>已复制 — 粘贴到对话框发送(人审门不绕过)</span>}
          </div>
        </div>}

        <div style={{ marginTop: 10, borderTop: '1px dashed var(--line)', paddingTop: 6 }}>
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
      </div>}
    </div>
  );
}
window.WwReviewReportCard = WwReviewReportCard;
