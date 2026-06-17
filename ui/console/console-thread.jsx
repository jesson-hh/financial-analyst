// console-thread.jsx — Task 3: 对话 UI 从 chat/app.jsx 移植

// ── Cite (app.jsx:2075-2084, 零改) ──
function Cite({ n }) {
  return (
    <sup style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: 14, height: 14, background: 'var(--yin)', color: 'var(--paper)',
      fontFamily: 'var(--serif)', fontSize: 8, fontWeight: 500,
      margin: '0 1px', verticalAlign: 2, cursor: 'pointer'
    }} title={`引用 ${n}`}>{n}</sup>
  );
}

// ── renderInline (app.jsx:3234-3246, 零改) ──
function renderInline(text) {
  // **bold**, `code`, *italic*, [§N] 引用 简易解析 (顺序: 粗体先于斜体, 避免吃掉 **)
  const parts = (text || '').split(/(\*\*[^*]+\*\*|`[^`]+`|\[§\d+\]|\*[^*\n]+\*)/g);
  return parts.map((p, i) => {
    if (!p) return null;
    let m;
    if (p.startsWith('**') && p.endsWith('**')) return <strong key={i} style={{ fontWeight: 600, color: 'var(--ink)' }}>{p.slice(2, -2)}</strong>;
    if (p.startsWith('`') && p.endsWith('`')) return <code key={i} style={{ fontFamily: 'var(--mono)', fontSize: '0.9em', background: 'var(--paper-2)', padding: '0 4px' }}>{p.slice(1, -1)}</code>;
    if ((m = p.match(/^\[§(\d+)\]$/))) return <Cite key={i} n={m[1]} />;
    if (p.length > 2 && p.startsWith('*') && p.endsWith('*')) return <em key={i} style={{ fontStyle: 'italic', color: 'var(--ink-1)' }}>{p.slice(1, -1)}</em>;
    return <span key={i}>{p}</span>;
  });
}

// ── renderChatMarkdown 及其助手 (app.jsx:3249-3349, 零改) ──
function renderChatMarkdown(text) {
  const lines = (text || '').split('\n');
  const out = [];
  let listBuf = [], listType = null;

  const flushList = (key) => {
    if (!listBuf.length) return;
    const ordered = listType === 'ol';
    out.push(
      <div key={key} style={{ margin: '4px 0' }}>
        {listBuf.map((it, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, fontSize: 14, color: 'var(--ink)', lineHeight: 1.7, margin: '3px 0', paddingLeft: 2 + it.indent * 16 }}>
            <span style={{ flexShrink: 0, color: 'var(--yin)', fontFamily: ordered ? 'var(--mono)' : 'inherit', fontSize: ordered ? 12 : 14, minWidth: ordered ? 16 : 8 }}>{ordered ? `${it.marker}.` : '·'}</span>
            <span style={{ flex: 1, minWidth: 0 }}>{renderInline(it.text)}</span>
          </div>
        ))}
      </div>
    );
    listBuf = []; listType = null;
  };

  // ── 表格 (LLM 常用 | a | b | + |---|---| 表格; 旧实现漏渲染成生管道符 → 列不对齐) ──
  const _isTableRow = (s) => { const t = (s || '').trim(); return t.includes('|') && (t.startsWith('|') || /\S\s*\|\s*\S/.test(t)); };
  const _isTableSep = (s) => { const t = (s || '').trim(); return t.includes('|') && /-/.test(t) && /^\|?[\s:|-]+\|?$/.test(t); };
  const _splitRow = (s) => { let t = (s || '').trim(); if (t.startsWith('|')) t = t.slice(1); if (t.endsWith('|')) t = t.slice(0, -1); return t.split('|').map(c => c.trim()); };
  const _alignOf = (c) => { const x = (c || '').trim(); const l = x.startsWith(':'), r = x.endsWith(':'); return l && r ? 'center' : r ? 'right' : 'left'; };
  const renderTable = (header, aligns, body, key) => out.push(
    <div key={key} style={{ overflowX: 'auto', margin: '10px 0' }}>
      <table className="serif" style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
        <thead>
          <tr>{header.map((h, ci) => (
            <th key={ci} style={{ textAlign: aligns[ci] || 'left', padding: '6px 16px 6px 0', borderBottom: '1.5px solid var(--ink-3)', color: 'var(--ink)', fontWeight: 600, whiteSpace: 'nowrap' }}>{renderInline(h)}</th>
          ))}</tr>
        </thead>
        <tbody>
          {body.map((row, ri) => (
            <tr key={ri}>{header.map((_, ci) => (
              <td key={ci} style={{ textAlign: aligns[ci] || 'left', padding: '5px 16px 5px 0', borderBottom: '1px solid var(--line)', color: 'var(--ink-2)', lineHeight: 1.6, verticalAlign: 'top' }}>{renderInline(row[ci] || '')}</td>
            ))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const trimmed = raw.trim();

    // 表格: 当前行是表行 且 下一行是分隔行 (|---|---|) → 整块渲成对齐 <table>
    if (_isTableRow(raw) && i + 1 < lines.length && _isTableSep(lines[i + 1])) {
      flushList('l' + i);
      const header = _splitRow(raw);
      const aligns = _splitRow(lines[i + 1]).map(_alignOf);
      const body = [];
      let j = i + 2;
      while (j < lines.length && lines[j].trim() !== '' && _isTableRow(lines[j]) && !_isTableSep(lines[j])) {
        body.push(_splitRow(lines[j]));
        j++;
      }
      renderTable(header, aligns, body, 't' + i);
      i = j - 1;
      continue;
    }

    const indent = (raw.match(/^\s*/)[0] || '').replace(/\t/g, '    ').length;
    const bulletM = trimmed.match(/^[*\-•·]\s+(.*)$/);
    const orderM = trimmed.match(/^(\d+)[.)]\s+(.*)$/);

    if (bulletM) {
      if (listType === 'ol') flushList('l' + i);
      listType = 'ul';
      listBuf.push({ text: bulletM[1], indent: Math.min(3, Math.floor(indent / 3)) });
      continue;
    }
    if (orderM) {
      if (listType === 'ul') flushList('l' + i);
      listType = 'ol';
      listBuf.push({ text: orderM[2], marker: orderM[1], indent: Math.min(3, Math.floor(indent / 3)) });
      continue;
    }
    flushList('l' + i);

    if (trimmed === '') { out.push(<div key={i} style={{ height: 8 }} />); continue; }
    // 标题 1–6 级一次性匹配 (旧实现只判 #/##/### → agent 速览答案常用 #### 作小节标题,
    // 会漏渲染成生 "#### xxx" 纯文本. 泛化到 #{1,6}, 1–3 级保持原视觉, 4–6 级递减).
    const headM = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (headM) {
      const lvl = headM[1].length;
      const fs = ({ 1: 16, 2: 15, 3: 14, 4: 14, 5: 13, 6: 13 })[lvl] || 14;
      const mt = lvl <= 2 ? 14 : 12;
      out.push(<div key={i} className="serif" style={{ fontSize: fs, fontWeight: 600, color: 'var(--ink)', margin: `${mt}px 0 4px` }}>{renderInline(headM[2])}</div>);
      continue;
    }
    if (trimmed.startsWith('> ')) { out.push(<blockquote key={i} className="serif" style={{ margin: '6px 0', padding: '4px 12px', borderLeft: '2px solid var(--ink-3)', color: 'var(--ink-2)', fontSize: 13, fontStyle: 'italic' }}>{renderInline(trimmed.slice(2))}</blockquote>); continue; }
    if (/^([-—*]\s?){3,}$/.test(trimmed)) { out.push(<hr key={i} style={{ border: 0, borderTop: '1px solid var(--line)', margin: '10px 0' }} />); continue; }
    out.push(<p key={i} className="serif" style={{ fontSize: 14, color: 'var(--ink)', lineHeight: 1.85, margin: '4px 0' }}>{renderInline(trimmed)}</p>);
  }
  flushList('l-end');
  return out;
}

// ── UserBubble (app.jsx:1641-1651;用户拍板:发出的对话靠左,不靠右) ──
function UserBubble({ text }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
      <div style={{
        maxWidth: '72%', padding: '12px 16px', background: 'var(--ink)',
        color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 14, lineHeight: 1.65,
        animation: 'fadeIn 200ms ease-out'
      }}>{text}</div>
    </div>
  );
}

// ── AiAvatar (app.jsx:1669-1671, 零改) ──
function AiAvatar() {
  return <div style={{ width: 28, height: 28, flex: '0 0 28px', background: 'var(--paper-2)', border: '1px solid var(--ink)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--ink)' }}>觀</div>;
}

// ── ToolChain (app.jsx:1674-1725, 适配:props改/删planning/折叠头label/删backendUrl+dispatch) ──
function ToolChain({ msg }) {
  const done = msg.chain.filter(c => c.status === 'done').length;
  const total = msg.chain.length;
  const elapsed = msg.chain.filter(c => c.status === 'done').reduce((s, c) => s + (c.t || 0), 0);
  const hasRunning = msg.chain.some(c => c.status === 'running');
  const allDone = total > 0 && done === total;
  // 默认 collapsed; 有 running 时展开; 全 done 后自动 collapse 不占视觉
  const [userExpanded, setUserExpanded] = React.useState(null);  // null = 自动, true/false = 用户覆盖
  const expanded = userExpanded !== null ? userExpanded : (hasRunning && !allDone);

  return (
    <div style={{ display: 'flex', gap: 14, animation: 'fadeIn 200ms ease-out' }}>
      <AiAvatar />
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* 透明背景, 无边框 — 跟用户聊天上下文融为一体 */}
        <div>
          <div onClick={() => setUserExpanded(!expanded)}
               style={{ padding: '4px 0', display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
                        color: 'var(--ink-3)', userSelect: 'none' }}>
            <span className="mono" style={{ fontSize: 10, color: allDone ? 'var(--ink-3)' : 'var(--yin)' }}>
              {expanded ? '▾' : '▸'}
            </span>
            <span className="mono" style={{ fontSize: 11, color: allDone ? 'var(--ink-3)' : 'var(--ink-2)' }}>
              {allDone ? `已用 ${total} 个工具 · ${elapsed.toFixed(1)}s` : `调用工具 · ${done}/${total} · ${elapsed.toFixed(1)}s`}
            </span>
          </div>
          {expanded && (
            <div style={{ padding: '2px 0 4px', borderLeft: '1px dashed var(--line)', marginLeft: 6, paddingLeft: 10 }}>
              {msg.chain.map((tl, i) => (
                <ToolRow key={i} i={i + 1} {...tl} last={i === msg.chain.length - 1} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── ToolRow (app.jsx:1772-1818, 适配:删onClick+cursor/删DeepReportProgress/pulse→pulseRing/加fail态) ──
function ToolRow({ i, name, cn, args, t, status, result, last }) {
  const running = status === 'running';
  const pending = status === 'pending';
  const cancelled = status === 'cancelled';
  const fail = status === 'fail';
  return (
    <div style={{ display: 'flex', gap: 14, padding: '8px 12px', alignItems: 'flex-start', position: 'relative', opacity: pending ? 0.4 : 1, transition: 'opacity 250ms', borderRadius: 8 }}>
      <div style={{ position: 'relative', width: 22, flex: '0 0 22px' }}>
        <div style={{
          width: 22, height: 22,
          background: running ? 'var(--yin)' : (pending || cancelled) ? 'transparent' : fail ? 'var(--yin)' : 'var(--ink)',
          color: (pending || cancelled) ? 'var(--ink-3)' : 'var(--paper)',
          border: (pending || cancelled) ? '1px dashed var(--ink-3)' : 'none',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'var(--serif)', fontSize: 11, fontWeight: 500,
          transition: 'background 250ms'
        }}>{cancelled ? '×' : fail ? '✗' : i}</div>
        {!last && <div style={{ position: 'absolute', top: 24, left: 10, bottom: -12, width: 2, background: 'var(--line)' }} />}
        {running && <div style={{ position: 'absolute', inset: -3, width: 28, height: 28, border: '1px solid var(--yin)', opacity: 0.5, animation: 'pulseRing 1.6s ease-in-out infinite' }} />}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <code style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)', fontWeight: 500, whiteSpace: 'nowrap' }}>{name}</code>
          <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{cn}</span>
          <span className="mono" style={{ fontSize: 10, color: fail ? 'var(--yin)' : 'var(--ink-3)', whiteSpace: 'nowrap', marginLeft: 'auto' }}>
            {running ? '⠋ 运行中…' : pending ? '— 等待' : cancelled ? '× 已取消' : fail ? '✗ 失败' : `✓ ${t}s`}
          </span>
        </div>
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2, wordBreak: 'break-all' }}>{args}</div>
        {!pending && !cancelled && (
          <div className="serif" style={{ fontSize: 12.5, color: running ? 'var(--ink-3)' : 'var(--ink-1)', marginTop: 5, fontStyle: running ? 'italic' : 'normal', display: 'flex', alignItems: 'center', gap: 6 }}>
            {!running && <span style={{ color: 'var(--ink-3)' }}>→</span>}
            <span style={{ flex: 1 }}>{running ? '正在抓取…' : result}</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── ConfirmModal (app.jsx:2660-2729, 适配:props改/resolve改/删'a'按钮/删fromBackend等/label+detail用WW.TOOL_CN) ──
function ConfirmModal({ confirm, onChoice }) {
  const c = confirm;
  const [sending, setSending] = React.useState('');
  const [err, setErr] = React.useState('');
  const resolve = async (ch) => {
    if (sending) return;
    setSending(ch); setErr('');
    try {
      const r = await onChoice(ch);
      if (!r || r.ok !== true) { setSending(''); setErr((r && r.reason) || '确认透传失败,请重试'); }
      // 成功后不本地关门:等 confirm_resolved 事件(单一事实源);sending 保持防双击
    } catch (e) { setSending(''); setErr('网络错误: ' + e); }
  };

  React.useEffect(() => {
    const k = (e) => {
      const key = e.key.toLowerCase();
      if (key === 'y') { e.preventDefault(); resolve('y'); }
      if (key === 'n') { e.preventDefault(); resolve('n'); }
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [sending]);

  return (
    <div onClick={() => {}} style={{
      position: 'fixed', inset: 0, background: 'rgba(28,24,20,0.55)', backdropFilter: 'blur(2px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100, animation: 'fadeIn 200ms ease-out'
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 520, background: 'var(--paper)', border: '2px solid var(--yin)',
        boxShadow: '0 24px 80px rgba(0,0,0,0.3)'
      }}>
        <div style={{ padding: '18px 24px 14px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'flex-start', gap: 14 }}>
          <div style={{ width: 40, height: 40, background: 'var(--yin)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 22, fontWeight: 500, flexShrink: 0 }}>⚠</div>
          <div style={{ flex: 1 }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--yin)', letterSpacing: '0.2em', marginBottom: 4 }}>
              等待工具确认
            </div>
            <div className="serif" style={{ fontSize: 17, color: 'var(--ink)', fontWeight: 500 }}>{(window.WW.TOOL_CN[c.tool] || c.tool)}</div>
          </div>
        </div>
        <div style={{ padding: '14px 24px', fontFamily: 'var(--serif)', fontSize: 13.5, color: 'var(--ink-1)', lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>
          {JSON.stringify(c.args, null, 1)}
        </div>
        {Array.isArray(c.facts) && c.facts.length > 0 && (
          <div style={{ margin: '0 24px 10px', padding: '10px 12px', background: 'var(--paper-2)', border: '1px solid var(--line)' }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.15em', marginBottom: 6 }}>机器核数 · 实时真值</div>
            {c.facts.map((f, i) => (
              <div key={i} className="mono" style={{ fontSize: 11.5, color: 'var(--ink-1)', lineHeight: 1.7 }}>{f}</div>
            ))}
          </div>
        )}
        {Array.isArray(c.precheck) && c.precheck.length > 0 && (
          <div style={{ margin: '0 24px 10px', padding: '10px 12px', border: '1px solid var(--yin)', background: 'rgba(140,30,20,0.05)' }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--yin)', letterSpacing: '0.15em', marginBottom: 6 }}>⚠ 预检 {c.precheck.length} 处 · 叙述与数据矛盾</div>
            {c.precheck.map((p, i) => (
              <div key={i} className="serif" style={{ fontSize: 12.5, color: 'var(--yin)', lineHeight: 1.7 }}>{p}</div>
            ))}
          </div>
        )}
        {err && <div className="serif" style={{ margin: '0 24px 8px', color: 'var(--yin)', fontSize: 12 }}>✗ {err}</div>}
        <div style={{ padding: '8px 24px 18px' }}>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.15em', marginBottom: 8 }}>选择操作</div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button onClick={() => resolve('y')}
              style={{ flex: 1, padding: '10px', background: 'var(--ink)', color: 'var(--paper)', border: 'none', fontFamily: 'var(--serif)', fontSize: 13, cursor: sending ? 'default' : 'pointer', opacity: sending ? 0.6 : 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
              <span>{sending === 'y' ? '已发送…' : '同意 · 一次'}</span>
              <span className="mono" style={{ fontSize: 9, opacity: 0.6 }}>Y</span>
            </button>
            <button onClick={() => resolve('n')}
              style={{ flex: 1, padding: '10px', background: 'transparent', color: 'var(--ink-2)', border: '1px solid var(--line)', fontFamily: 'var(--serif)', fontSize: 13, cursor: sending ? 'default' : 'pointer', opacity: sending ? 0.6 : 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
              <span>{sending === 'n' ? '已发送…' : '拒绝'}</span>
              <span className="mono" style={{ fontSize: 9, opacity: 0.6 }}>N · ESC</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── WwReportCard ──
function WwReportCard({ item, onOpen }) {
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <AiAvatar />
      <div style={{ border: '1px solid var(--yin)', padding: '10px 14px', fontSize: 13, fontFamily: 'var(--serif)', background: 'var(--paper-2)' }}>
        📄 {item.name} 深度研报已生成
        <span className="hover-link" onClick={() => onOpen(item)} style={{ marginLeft: 12, color: 'var(--yin)', cursor: 'pointer', fontSize: 12 }}>查看全文 ↗</span>
      </div>
    </div>
  );
}

// ── WwLauncher ── 输入坞左侧 ◫:手动呼出右栏功能页(选股/工作流/经验卡/图谱),与对话「调出选股」等价
function WwLauncher({ activated, onOpenPage }) {
  const PAGES = window.WW.PAGES;
  const [open, setOpen] = React.useState(false);
  const pick = (p) => { setOpen(false); onOpenPage(p); };
  return (
    <div style={{ position: 'relative', flexShrink: 0 }}>
      <div onClick={() => setOpen(o => !o)} title="呼出功能面板"
        style={{ height: 23, padding: '0 10px', border: '1px solid ' + (open ? 'var(--ink)' : 'var(--ink-2)'), color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontFamily: 'var(--serif)', letterSpacing: 2, cursor: 'pointer', borderRadius: 4, userSelect: 'none', whiteSpace: 'nowrap', background: open ? 'var(--paper-2)' : 'var(--paper-2)' }}>
        <span className="mono" style={{ fontSize: 13, letterSpacing: 0 }}>◫</span>功能</div>
      {open && (
        <React.Fragment>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 30 }} />
          <div style={{ position: 'absolute', bottom: 32, left: 0, zIndex: 31, width: 228, background: 'var(--paper)', border: '1px solid var(--ink)', boxShadow: '4px 4px 0 rgba(0,0,0,0.08)', padding: '5px 0', animation: 'fadeIn 0.12s ease' }}>
            <div style={{ padding: '5px 14px 8px', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 2, borderBottom: '1px solid var(--line)', fontFamily: 'var(--serif)' }}>呼出功能 · 右栏</div>
            {Object.keys(PAGES).map(p => (
              <div key={p} onClick={() => pick(p)}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', cursor: 'pointer', fontSize: 13, fontFamily: 'var(--serif)' }}
                onMouseEnter={e => { e.currentTarget.style.background = 'var(--paper-2)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}>
                <span className="seal" style={{ fontSize: 11 }}>{PAGES[p].label[0]}</span>
                <span style={{ flex: 1, letterSpacing: 1 }}>{PAGES[p].label}</span>
                <span style={{ fontSize: 10, color: activated.indexOf(p) >= 0 ? 'var(--yin)' : 'var(--ink-3)' }}>{activated.indexOf(p) >= 0 ? '● 已开' : '○'}</span>
              </div>
            ))}
            <div style={{ padding: '8px 14px 5px', fontSize: 10.5, color: 'var(--ink-3)', borderTop: '1px solid var(--line)' }}>也可直接下令:「调出选股」</div>
          </div>
        </React.Fragment>
      )}
    </div>
  );
}

// ── WwSessBar ── 对话区顶栏:本会话身份 + 本会话任务,随会话切换各不相同(用户拍板:每个对话都有不同的顶栏)
// 三期:任务芯片旁挂哨兵徽章(平台级研判,unread>0 才显形),任务面板尾部挂「哨兵研判 · 全局」分区
function WwSessBar({ state, onOpenReport, onRename, sentry, markSentrySeen, onOpenPage, onSentryFocus }) {
  const [open, setOpen] = React.useState(false);
  const [edit, setEdit] = React.useState(null);          // 改名草稿 string|null
  React.useEffect(() => { if (open && markSentrySeen) markSentrySeen(); }, [open]);  // 打开面板即记已读
  const title = (state.meta && state.meta.title) || '新对话';
  const bg = Object.values(state.bgTasks || {}).sort((a, b) => String(b.ts || '').localeCompare(String(a.ts || '')));
  const n = (state.busy ? 1 : 0) + bg.filter(t => t.status === 'running').length;
  const cur = state.plan.find(t => t.status === 'in_progress');
  const reportFor = (code) => {                          // done 研报 → 从事件流找回 report_md 供「查看」
    for (let i = state.events.length - 1; i >= 0; i--) {
      const ev = state.events[i];
      if (ev.type === 'tool_result' && ev.artifact && ev.artifact.kind === 'report_md' && ev.artifact.payload.code === code) return ev.artifact.payload;
    }
    return null;
  };
  const dot = (c, run) => <span style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: c, animation: run ? 'pulse 1.4s infinite' : 'none' }} />;
  const commit = () => { const v = (edit || '').trim(); setEdit(null); if (v && v !== title) onRename(v); };
  return (
    <div style={{ position: 'relative', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 9, height: 38, padding: '0 18px', borderBottom: '1px solid var(--line)', background: 'var(--paper)' }}>
      {edit == null ? (
        <React.Fragment>
          <span title={title} style={{ fontFamily: 'var(--serif)', fontSize: 13.5, fontWeight: 600, letterSpacing: 1, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '42%' }}>{title}</span>
          <span title="改名" onClick={() => setEdit(title)} style={{ fontSize: 11, color: 'var(--ink-3)', cursor: 'pointer' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--ink)'; }} onMouseLeave={e => { e.currentTarget.style.color = 'var(--ink-3)'; }}>✎</span>
        </React.Fragment>
      ) : (
        <input autoFocus value={edit} onChange={e => setEdit(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEdit(null); }} onBlur={commit}
          style={{ border: '1px solid var(--ink-2)', background: 'var(--paper)', padding: '3px 8px', fontSize: 12.5, fontFamily: 'var(--serif)', color: 'var(--ink)', outline: 'none', width: 240 }} />
      )}
      <span style={{ flex: 1 }} />
      {state.busy && (
        <span style={{ fontSize: 11, color: 'var(--zhu)', display: 'flex', alignItems: 'center', gap: 6, overflow: 'hidden', whiteSpace: 'nowrap' }}>
          {dot('var(--zhu)', true)}{cur ? cur.text.slice(0, 24) : '运筹中…'}
        </span>
      )}
      {sentry && sentry.unread > 0 && (
        <span onClick={() => setOpen(true)} title="落子哨兵新研判(平台级,点开任务面板查看)"
          style={{ display: 'flex', alignItems: 'center', gap: 5, height: 24, padding: '0 10px', border: '1px solid var(--zhu)', borderRadius: 12, cursor: 'pointer', fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '.04em', color: 'var(--zhu)', background: 'var(--paper-2)', userSelect: 'none' }}>
          {dot('var(--zhu)', true)}哨 · {sentry.unread}
        </span>
      )}
      <span onClick={() => setOpen(o => !o)} title="本会话任务与后台任务"
        style={{ display: 'flex', alignItems: 'center', gap: 6, height: 24, padding: '0 11px', border: '1px solid ' + (n ? 'var(--yin)' : 'var(--line)'), borderRadius: 12, cursor: 'pointer', fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '.04em', color: n ? 'var(--yin)' : 'var(--ink-3)', background: 'var(--paper-2)', userSelect: 'none' }}>
        {dot(n ? 'var(--zhu)' : 'var(--line)', !!n)}任务{n ? ' · ' + n : ''}
      </span>
      {open && <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />}
      {open && (
        <div style={{ position: 'absolute', top: 38, right: 12, width: 360, zIndex: 41, background: 'var(--paper)', border: '1px solid var(--ink)', boxShadow: '4px 4px 0 rgba(0,0,0,.08)', maxHeight: '60vh', overflowY: 'auto', animation: 'fadeIn .12s ease' }}>
          <div style={{ padding: '8px 14px', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 2, borderBottom: '1px solid var(--line)', fontFamily: 'var(--serif)' }}>任务 · {title.slice(0, 16)}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', fontSize: 12.5, fontFamily: 'var(--serif)', color: 'var(--ink-1)', borderBottom: bg.length ? '1px solid var(--line-soft)' : 'none' }}>
            {dot(state.busy ? 'var(--zhu)' : 'var(--line)', state.busy)}
            <span style={{ flex: 1 }}>当前对话:{state.busy ? (cur ? '▶ ' + cur.text : '运筹中…') : '空闲'}</span>
          </div>
          {bg.map(t => (
            <div key={t.task_id} style={{ padding: '9px 14px', borderBottom: '1px solid var(--line-soft)', fontSize: 12, opacity: t.status === 'done' ? 0.75 : 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--ink-1)', fontFamily: 'var(--serif)' }}>
                {dot(t.status === 'running' ? 'var(--zhu)' : t.status === 'error' ? 'var(--yin)' : 'var(--dai)', t.status === 'running')}
                <span style={{ flex: 1 }}>深度研报 · {t.code}</span>
                {t.status === 'done' && t.ok !== false && reportFor(t.code) && (
                  <span className="hover-link" style={{ color: 'var(--yin)', cursor: 'pointer', fontSize: 11 }}
                    onClick={() => { const p = reportFor(t.code); setOpen(false); onOpenReport({ path: p.path, code: p.code, name: p.name || p.code }); }}>查看 ↗</span>
                )}
              </div>
              <div style={{ marginTop: 3, fontSize: 10.5, color: t.status === 'error' ? 'var(--yin)' : 'var(--ink-3)', paddingLeft: 14 }}>{t.note}</div>
              {t.status === 'running' && t.progress != null && (
                <div style={{ margin: '6px 0 1px 14px', height: 3, background: 'var(--line-soft)', position: 'relative' }}>
                  <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: (t.progress * 100) + '%', background: 'var(--jin)' }} />
                </div>
              )}
            </div>
          ))}
          {!bg.length && !state.busy && <div style={{ padding: '12px 14px', fontSize: 11.5, color: 'var(--ink-3)' }}>暂无任务——下达指令或启动深度研报后挂在这里。</div>}
          {/* 三期:哨兵研判分区(平台级事实,非本会话事件流;点条目 onSentryFocus = handoff 本票 + 调出落子页点对点聚焦) */}
          <div style={{ padding: '8px 14px 6px', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 2, borderTop: '1px solid var(--line)', fontFamily: 'var(--serif)' }}>哨兵研判 · 全局<span style={{ letterSpacing: 0, marginLeft: 8 }}>(非本会话)</span></div>
          {((sentry && sentry.items) || []).slice(0, 8).map((it, i) => (
            <div key={it.id || ('sd' + i)} onClick={() => { setOpen(false); if (onSentryFocus) onSentryFocus(it); else if (onOpenPage) onOpenPage('seats'); }}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', borderBottom: '1px solid var(--line-soft)', fontSize: 11.5, fontFamily: 'var(--serif)', color: 'var(--ink-1)', cursor: 'pointer' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--paper-2)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}>
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {(it.kind === 'decide' ? '研判' : '条件单')} · {it.name || it.code || '—'}{it.direction ? ' · ' + it.direction : ''}{it.confidence != null ? ' 置信' + it.confidence : ''}
              </span>
              <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', flexShrink: 0 }}>{String(it.ts || '').slice(5, 16)}</span>
            </div>
          ))}
          {(!sentry || !(sentry.items || []).length) && <div style={{ padding: '10px 14px', fontSize: 11, color: 'var(--ink-3)' }}>暂无研判——落子哨兵出手后挂在这里。</div>}
        </div>
      )}
    </div>
  );
}

// ── WwThread ──
function WwThread({ state, onSend, onConfirm, onOpenReport, onOpenPage, activatedPages, onRename, sentry, markSentrySeen, onSentryFocus }) {
  const [draft, setDraft] = React.useState('');
  const scrollRef = React.useRef(null);
  const stickRef = React.useRef(true);
  const items = window.WW.deriveItems(state.events, state.busy);
  React.useEffect(() => {                                   // 粘底(app.jsx:1565-1577 语义)
    const el = scrollRef.current; if (!el) return;
    if (stickRef.current) el.scrollTop = el.scrollHeight;
  }, [state.events.length]);
  React.useEffect(() => { stickRef.current = true; }, [state.sid]);
  const onScroll = () => { const el = scrollRef.current; if (el) stickRef.current = (el.scrollHeight - el.scrollTop - el.clientHeight) < 80; };
  const send = () => { const t = draft.trim(); if (!t || state.busy) return; setDraft(''); onSend(t); };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }} className="paper-bg">
      <WwSessBar state={state} onOpenReport={onOpenReport} onRename={onRename} sentry={sentry} markSentrySeen={markSentrySeen} onOpenPage={onOpenPage} onSentryFocus={onSentryFocus} />
      <div ref={scrollRef} onScroll={onScroll} style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '24px 40px', display: 'flex', flexDirection: 'column', gap: 18, minHeight: 0 }}>
        {items.length === 0 && (
          <div style={{ textAlign: 'center', color: 'var(--ink-3)', fontSize: 13, marginTop: 80, fontFamily: 'var(--serif)', letterSpacing: 1 }}>
            对观澜下令——选股、回测、研报、研判、经验沉淀,一句话即可。
          </div>
        )}
        {items.map(m => {
          if (m.kind === 'user') return <UserBubble key={m.id} text={m.text} />;
          if (m.kind === 'chain') return <ToolChain key={m.id} msg={m} />;
          if (m.kind === 'answer') return (
            <div key={m.id} style={{ display: 'flex', gap: 12 }}>
              <AiAvatar />
              <div style={{ flex: 1, minWidth: 0, fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--ink)', padding: '4px 0' }}>
                {renderChatMarkdown(m.text)}
                {m.streaming && <span style={{ display: 'inline-block', width: 6, height: 14, background: 'var(--ink)', marginLeft: 4, verticalAlign: -2, animation: 'blink 1s steps(2) infinite' }} />}
              </div>
            </div>);
          if (m.kind === 'report') return <WwReportCard key={m.id} item={m} onOpen={onOpenReport} />;
          if (m.kind === 'condense') return (
            <div key={m.id} title={m.summary} style={{ textAlign: 'center', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 2, fontFamily: 'var(--serif)' }}>—— 前文已压缩入摘要 ——</div>);
          if (m.kind === 'error') return <div key={m.id} style={{ marginLeft: 40, color: 'var(--yin)', fontSize: 12 }}>✗ {m.note}</div>;
          return null;
        })}
        {state.busy && (!items.length || items[items.length - 1].kind !== 'answer') && (
          <div style={{ display: 'flex', gap: 12 }}><AiAvatar /><div style={{ fontSize: 12, color: 'var(--ink-3)', fontStyle: 'italic', padding: '6px 0' }}>⠋ 运筹中…</div></div>
        )}
      </div>
      {/* 输入坞:chat Composer 视觉(app.jsx:2284-2294),砍 slash/@/上传/模式 pill */}
      <div style={{ padding: '10px 40px 18px' }}>
        <div style={{ border: '1px solid var(--line)', borderRadius: 13, padding: '11px 13px 11px 17px', background: 'var(--paper)', display: 'flex', alignItems: 'flex-end', gap: 10 }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--ink-2)'; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--line)'; }}>
          <WwLauncher activated={activatedPages || []} onOpenPage={onOpenPage} />
          <textarea rows={1} value={draft} onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder={state.busy ? '执行中——完成后再下一令' : '对观澜下令…(Enter 发送,Shift+Enter 换行)'}
            style={{ flex: 1, border: 0, outline: 0, resize: 'none', background: 'transparent', color: 'var(--ink)', fontFamily: 'var(--serif)', fontSize: 14, lineHeight: 1.6, minHeight: 22, maxHeight: 120 }} />
          <div onClick={send} className="mono" style={{ width: 27, height: 23, border: '1px solid ' + (state.busy ? 'var(--line)' : 'var(--ink)'), color: state.busy ? 'var(--ink-3)' : 'var(--ink)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, cursor: state.busy ? 'default' : 'pointer', borderRadius: 4 }}>↵</div>
        </div>
      </div>
      {state.confirm && <ConfirmModal confirm={state.confirm} onChoice={onConfirm} />}
    </div>
  );
}
window.WwThread = WwThread;

// 导出给 drawer 文件用
window.WwMd = { renderInline, renderChatMarkdown, Cite, AiAvatar };
