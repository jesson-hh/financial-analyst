// console-drawer.jsx — Task 4: 研报抽屉从 chat/app.jsx 移植

// 取用 thread 文件导出的共享渲染函数
const { renderInline, AiAvatar } = window.WwMd;

// ── triggerDownload (app.jsx:3397-3403, 零改) ──
function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 1000);
}

// ── ReportMarkdown (app.jsx:3153-3232, 零改) ──
function ReportMarkdown({ text, streaming }) {
  // 极简, 不引入 markdown 库
  const lines = text.split('\n');
  const out = [];
  let inTable = false, tableRows = [];

  const flushTable = (key) => {
    if (tableRows.length === 0) return;
    const [headerRow, , ...rows] = tableRows;
    out.push(
      <table key={key} style={{ borderCollapse: 'collapse', width: '100%', margin: '12px 0', fontFamily: 'var(--mono)', fontSize: 11 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--ink)' }}>
            {headerRow.map((c, i) => <th key={i} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '6px 8px', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink-2)', fontWeight: 500 }}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} style={{ borderBottom: '1px solid var(--line-soft)' }}>
              {r.map((c, ci) => <td key={ci} style={{ textAlign: ci === 0 ? 'left' : 'right', padding: '6px 8px', color: 'var(--ink-1)' }}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    );
    tableRows = [];
  };

  lines.forEach((line, i) => {
    if (line.startsWith('|')) {
      const cells = line.split('|').slice(1, -1).map(c => c.trim());
      tableRows.push(cells);
      inTable = true;
      return;
    } else if (inTable) {
      flushTable('t' + i);
      inTable = false;
    }

    if (line.startsWith('# ')) {
      out.push(<h1 key={i} className="serif" style={{ fontSize: 22, fontWeight: 600, color: 'var(--ink)', margin: '0 0 8px', letterSpacing: '-0.005em' }}>{line.slice(2)}</h1>);
    } else if (line.startsWith('## ')) {
      out.push(<h2 key={i} className="serif" style={{ fontSize: 16, fontWeight: 500, color: 'var(--ink)', margin: '20px 0 8px', display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{line.slice(3, 5)}</span>
        <span>{line.slice(5)}</span>
      </h2>);
    } else if (line.startsWith('### ')) {
      out.push(<h3 key={i} className="serif" style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink-1)', margin: '12px 0 4px' }}>{renderInline(line.slice(4))}</h3>);
    } else if (line.startsWith('---')) {
      out.push(<hr key={i} style={{ border: 0, borderTop: '1px solid var(--line)', margin: '14px 0' }} />);
    } else if (line.startsWith('> ')) {
      out.push(<blockquote key={i} className="serif" style={{ margin: '8px 0', padding: '6px 12px', borderLeft: '2px solid var(--ink-3)', color: 'var(--ink-2)', fontSize: 11.5, fontStyle: 'italic' }}>{renderInline(line.slice(2))}</blockquote>);
    } else if (line.match(/^\s*[-*]\s+/)) {
      const bm = line.match(/^(\s*)[-*]\s+(.*)$/);
      const ind = Math.floor(((bm[1] || '').replace(/\t/g, '  ').length) / 2);
      out.push(<div key={i} style={{ display: 'flex', gap: 8, fontSize: 13, color: 'var(--ink)', lineHeight: 1.75, margin: '2px 0', paddingLeft: 4 + ind * 16 }}>
        <span style={{ flexShrink: 0, color: 'var(--yin)' }}>·</span>
        <span style={{ flex: 1, minWidth: 0 }}>{renderInline(bm[2])}</span>
      </div>);
    } else if (line.match(/^\d+\. /)) {
      out.push(<div key={i} className="serif" style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.75, margin: '3px 0', paddingLeft: 4 }}>
        {renderInline(line)}
      </div>);
    } else if (line.startsWith('**')) {
      out.push(<div key={i} className="serif" style={{ fontSize: 13, color: 'var(--ink-1)', lineHeight: 1.85, margin: '2px 0' }}>{renderInline(line)}</div>);
    } else if (line.trim() === '') {
      out.push(<div key={i} style={{ height: 6 }} />);
    } else {
      out.push(<p key={i} className="serif" style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.85, margin: '2px 0', textWrap: 'pretty' }}>{renderInline(line)}</p>);
    }
  });
  if (inTable) flushTable('t-end');

  return (
    <div>
      {out}
      {streaming && <span style={{ display: 'inline-block', width: 6, height: 14, background: 'var(--ink)', verticalAlign: -2, animation: 'blink 1s steps(2) infinite' }} />}
    </div>
  );
}

// ── WwDrawer (app.jsx:2936-3150 的 ReportDrawer 改名+适配) ──
// 适配: 删mock步骤分支/删轻量详情分支/dispatch→onClose/保留GL.put/保留计时器+走马灯
function WwDrawer({ drawer, onClose }) {
  const sym = drawer.sym;
  // 真·已用时计时器 (每秒走字)
  const [now, setNow] = React.useState(Date.now());
  React.useEffect(() => {
    if (drawer.status !== 'running') return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [drawer.status]);
  const elapsed = drawer.startedAt ? Math.max(0, Math.floor((now - drawer.startedAt) / 1000)) : 0;
  const mmss = (n) => `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
  // 「加入研究档案」→ GL 档案库(type:'research' 真物料, 无示例徽章)→ 图谱支柱/落子料库立即可见。
  // id 按 代码+日期 确定性生成 → 同日重复点击幂等(GL.put 同 id 即更新)。
  const [archived, setArchived] = React.useState(false);
  const archiveReport = () => {
    if (drawer.status !== 'done' || !window.GL || !sym) return;
    const day = new Date().toISOString().slice(0, 10);
    window.GL.put({
      type: 'research', id: `rs_report_${sym.code || 'x'}_${day}`,
      title: `${sym.name}${sym.name !== sym.code ? `(${sym.code})` : ''} 深度研报`,
      kind: '研报', from: '觀瀾 · run_report', status: 'raw',
      path: drawer.path || null, date: day,
    });
    setArchived(true);
  };

  return (
    <div className="paper-bg" style={{
      // top:44 = 全局导航条高度(guanlan-nav sticky z9000 会压住 top:0 的抽屉头, 标题/关闭键被挡)
      position: 'fixed', top: 44, right: 0, bottom: 0, width: 540,
      background: 'var(--paper)', borderLeft: '1px solid var(--line)',
      boxShadow: '-20px 0 60px rgba(0,0,0,0.18)',
      zIndex: 95, display: 'flex', flexDirection: 'column',
      animation: 'slideInRight 350ms cubic-bezier(.2,.7,.3,1)',
    }}>
      {/* 头 */}
      <div style={{ padding: '16px 22px 12px', borderBottom: '2px solid var(--ink)', display: 'flex', alignItems: 'flex-start', gap: 14, flexShrink: 0 }}>
        <div style={{ width: 38, height: 38, background: 'var(--yin)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 18, flexShrink: 0 }}>
          {drawer.status === 'done' ? '✓' : '⏳'}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 10, color: 'var(--yin)', letterSpacing: '0.2em', marginBottom: 3 }}>
            {drawer.status === 'done' ? '深度研报 · 已完成' : '深度研报 · 后台运行'}
          </div>
          <div className="serif" style={{ fontSize: 17, color: 'var(--ink)', fontWeight: 500 }}>{sym.name}{sym.name !== sym.code ? ` · ${sym.code}` : ''}</div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3 }}>
            {drawer.status === 'done'
              ? (drawer.startedAt ? `已完成 · 用时 ${mmss(elapsed)}` : '已完成')
              : `生成中 · 已用时 ${mmss(elapsed)} · 真实约 5-8 分钟`}
          </div>
        </div>
        <button onClick={() => onClose()}
          style={{ background: 'transparent', border: 'none', color: 'var(--ink-3)', cursor: 'pointer', fontSize: 18, lineHeight: 1, padding: 0 }}>×</button>
      </div>

      {/* 进度: 真·已用时计时器 + 走马灯 */}
      {drawer.status !== 'done' && (
        <div style={{ padding: '18px 22px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <div style={{ position: 'relative', width: 16, height: 16, flexShrink: 0 }}>
              <div style={{ width: 10, height: 10, margin: 3, background: 'var(--yin)' }} />
              <div style={{ position: 'absolute', inset: 0, border: '1px solid var(--yin)', opacity: 0.4, animation: 'pulse 1.6s ease-in-out infinite' }} />
            </div>
            <span className="mono" style={{ fontSize: 22, color: 'var(--ink)', fontWeight: 500 }}>{mmss(elapsed)}</span>
            <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)' }}>已用时 · 真实约 5-8 分钟</span>
          </div>
          {/* 不确定进度条 (走马灯) */}
          <div style={{ height: 3, background: 'var(--paper-2)', overflow: 'hidden', position: 'relative', marginBottom: 12 }}>
            <div style={{ position: 'absolute', height: '100%', width: '35%', background: 'var(--yin)', animation: 'slideInRight 1.4s ease-in-out infinite alternate' }} />
          </div>
          <div className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7 }}>
            后台正在<strong style={{ color: 'var(--ink)' }}>现场训练 LightGBM + Flow Matching</strong>、跑多空辩论与风控审查，一次性算完后整篇返回（不逐字流式）。本次将产出：
          </div>
          <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--ink-3)', fontFamily: 'var(--serif)', lineHeight: 1.8 }}>
            综合评级 · Variance Table · 基本面 · 技术与情绪 · 量化共识(LGB分位) · 多空辩论 · 风控审查 · 操作建议
          </div>
        </div>
      )}

      {/* 报告正文 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: drawer.text ? '20px 28px 24px' : '20px 28px' }}>
        {drawer.text
          ? <ReportMarkdown text={drawer.text} streaming={drawer.status === 'running'} />
          : drawer.status !== 'done' && (
            <div className="serif" style={{ fontSize: 13, color: 'var(--ink-3)', textAlign: 'center', padding: '40px 0', fontStyle: 'italic' }}>
              研报在后台计算，完成后整篇展示在此（可关掉抽屉，跑完会自动填充）…
            </div>
          )
        }
      </div>

      {/* 操作行 */}
      <div style={{ padding: '10px 22px', borderTop: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, background: 'rgba(241,234,217,0.4)' }}>
        <button
          disabled={drawer.status !== 'done'}
          onClick={() => {
            const blob = new Blob([drawer.text], { type: 'text/markdown;charset=utf-8' });
            triggerDownload(blob, `深度研报-${sym.name}-${sym.code}.md`);
          }}
          style={{ background: 'var(--ink)', color: 'var(--paper)', border: 'none', padding: '6px 14px', fontFamily: 'var(--serif)', fontSize: 12, cursor: drawer.status === 'done' ? 'pointer' : 'not-allowed', opacity: drawer.status === 'done' ? 1 : 0.4 }}>
          ↧ 导出 markdown
        </button>
        <button disabled={drawer.status !== 'done' || archived} onClick={archiveReport}
          title={window.GL ? '写入共享档案库 → 研究图谱 / 落子料库可见' : '档案库未加载'}
          style={{ background: archived ? 'rgba(74,107,92,0.12)' : 'transparent', color: archived ? 'var(--dai)' : 'var(--ink-1)', border: '1px solid ' + (archived ? 'var(--dai)' : 'var(--line)'), padding: '6px 14px', fontFamily: 'var(--serif)', fontSize: 12, cursor: archived ? 'default' : 'pointer', opacity: drawer.status === 'done' ? 1 : 0.4 }}>
          {archived ? '✓ 已入档案库' : '加入研究档案'}
        </button>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>tool: run_report</span>
      </div>
    </div>
  );
}
window.WwDrawer = WwDrawer;
