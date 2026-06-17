// 观澜 · 研究图谱 — 总览首页 / 导航中枢
// 读 window.GL(共享档案库),展示四支柱的物料流动与溯源,带上下文跳转各模块。
const { useState, useEffect } = React;

const WW_EMBED = new URLSearchParams(location.search).get('embed') === '1';

// href 必须 ../<module>/ 相对路径:本页挂在 /ui/graph/,裸文件名相对本目录解析全 404(互通审计 P1⑧;对照 guanlan-nav.js 同款写法)
const PILLARS = [
  { key: 'research', z: '问', cn: '对话 · 研报', en: 'DIALOGUE', sub: '提出问题 · 生成研报观点', href: '../chat/观澜 · 交互原型.html', ch: 'dialogue', color: 'var(--ink-1)', type: 'research' },
  { key: 'card', z: '炼', cn: '经验卡', en: 'DISTILL', sub: '文本提炼 · 验证沉淀', href: '../cards/观澜 · 经验验证区.html', ch: 'validation', color: 'var(--yin)', type: 'card' },
  { key: 'factor', z: '验', cn: '因子 · 工作流', en: 'QUANT', sub: '因子链 · 回测验证', href: '../factor/观澜 · AI 工作流.html', ch: 'workflow', color: 'var(--dai)', type: 'factor' },
  { key: 'seat', z: '用', cn: '席位 · 落子', en: 'AGENT', sub: '装配席位 · 实测盯盘', href: '../seats/观澜 · 落子.html', ch: 'cockpit', color: 'var(--jin)', type: 'seat' },
];
const TYPE_CN = { research: '研报', card: '经验卡', factor: '因子', seat: '席位', decision: '落子' };
const TYPE_HREF = { research: '../chat/观澜 · 交互原型.html', card: '../cards/观澜 · 经验验证区.html', factor: '../factor/观澜 · AI 工作流.html', seat: '../seats/观澜 · 落子.html', decision: '../seats/观澜 · 落子.html' };
const TYPE_CH = { card: 'validation', factor: 'workflow', seat: 'cockpit', research: 'dialogue', decision: 'cockpit' };

function statusColor(a) {
  if (a.verdict === '存疑') return 'var(--jin)';
  if (a.verdict === '驳回') return 'var(--yin)';
  if (a.status === 'deployed') return 'var(--jin)';
  if (a.status === 'validated' || a.verdict === '通过') return 'var(--dai)';
  if (a.status === 'draft') return 'var(--zhu)';
  return 'var(--ink-3)';
}
const focusPayload = (a) => a.type === 'card' ? { focusCard: a.id, focusCardName: a.title } : a.type === 'seat' ? { focusSeat: a.id } : a.type === 'factor' ? { factor: a.expr, expr: a.expr, name: a.title } : { focusId: a.id };   // focusCardName 对齐 validation 匹配键;expr 对齐 workflow 接收键

function GraphApp() {
  const [tick, setTick] = useState(0);
  const [sel, setSel] = useState(null);
  useEffect(() => GL.on(() => setTick(t => t + 1)), []);
  const stats = GL.stats();
  const selA = sel ? GL.get(sel) : null;

  const open = (a) => GL.go(TYPE_HREF[a.type], TYPE_CH[a.type], focusPayload(a));

  return (
    <div className="paper-bg" style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* 顶栏 */}
      {!WW_EMBED && (
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '0 22px', height: 56, borderBottom: '1px solid var(--line)', background: 'rgba(241,234,217,0.7)', flexShrink: 0 }}>
        <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.15 }}>
          <span className="serif" style={{ fontSize: 16, fontWeight: 600, letterSpacing: '.05em' }}>研究圖譜</span>
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.2em', marginTop: 2 }}>RESEARCH GRAPH · 共享档案库</span>
        </div>
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)', marginLeft: 14 }}>物料 <b style={{ color: 'var(--ink)' }}>{stats.total}</b> 件 · 刷新不丢 · 跨页同步</span>
      </div>
      )}

      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        <div style={{ maxWidth: 1180, margin: '0 auto', padding: '26px 24px 40px' }}>
          <div className="serif" style={{ fontSize: 14, color: 'var(--ink-2)', textAlign: 'center', marginBottom: 22, lineHeight: 1.7, textWrap: 'pretty' }}>
            一条河:<b style={{ color: 'var(--ink)' }}>对话出研报</b> → <b style={{ color: 'var(--yin)' }}>炼成经验卡</b> → <b style={{ color: 'var(--dai)' }}>工作流验证</b> → <b style={{ color: 'var(--jin)' }}>席位实测落子</b> → 复盘回灌。点任意物料,带着它跳进对应模块。
          </div>

          {/* 四支柱河流 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0, alignItems: 'stretch', position: 'relative' }}>
            {PILLARS.map((p, i) => {
              const items = GL.all(p.type);
              return (
                <React.Fragment key={p.key}>
                  <div className="gl-hover" onClick={() => GL.go(p.href, p.ch, {})} style={{ position: 'relative', cursor: 'pointer', border: '1px solid var(--line)', borderTop: '3px solid ' + p.color, borderRadius: 12, background: 'var(--paper)', padding: '16px 15px', boxShadow: '0 2px 10px rgba(28,24,20,0.05)', margin: '0 9px', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 4 }}>
                      <span className="serif" style={{ width: 30, height: 30, borderRadius: 8, background: p.color, color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, fontWeight: 600, flexShrink: 0 }}>{p.z}</span>
                      <div style={{ minWidth: 0 }}>
                        <div className="serif" style={{ fontSize: 14.5, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap' }}>{p.cn}</div>
                        <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '.14em' }}>{p.en}</div>
                      </div>
                      <span className="mono" style={{ marginLeft: 'auto', fontSize: 18, fontWeight: 600, color: p.color }}>{items.length}</span>
                    </div>
                    <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginBottom: 11 }}>{p.sub}</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                      {items.slice(0, 6).map(a => (
                        <div key={a.id} className="gl-chip" onClick={(e) => { e.stopPropagation(); setSel(a.id); }} title="点击看溯源 · 再点跳转"
                          style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 7px', borderRadius: 6, border: '1px solid ' + (sel === a.id ? p.color : 'var(--line-soft)'), background: sel === a.id ? 'rgba(28,24,20,0.04)' : 'transparent', cursor: 'pointer' }}>
                          <span style={{ width: 6, height: 6, borderRadius: '50%', background: statusColor(a), flexShrink: 0 }} />
                          <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, minWidth: 0 }}>{a.title}</span>
                          {a.demo && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '0 4px', flexShrink: 0 }}>示例</span>}
                          {a.ic && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', flexShrink: 0 }}>IC {a.ic}</span>}
                        </div>
                      ))}
                      {items.length > 6 && <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', paddingLeft: 7 }}>+{items.length - 6} 更多…</span>}
                    </div>
                    <span className="mono" style={{ marginTop: 'auto', paddingTop: 11, fontSize: 9.5, color: p.color }}>进入模块 →</span>
                  </div>
                  {i < PILLARS.length - 1 && (
                    <div style={{ position: 'absolute', left: `calc(${(i + 1) * 25}% - 9px)`, top: 46, transform: 'translateX(-50%)', zIndex: 2, color: 'var(--ink-3)', fontSize: 16, pointerEvents: 'none' }}>→</div>
                  )}
                </React.Fragment>
              );
            })}
          </div>

          {/* 回灌 */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 9, margin: '14px 0 4px', padding: '8px 16px', border: '1px dashed var(--line)', borderRadius: 10, background: 'rgba(28,24,20,0.015)' }}>
            <span className="mono" style={{ fontSize: 11, color: 'var(--jin)' }}>↺ 复盘回灌</span>
            <span className="serif" style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>席位实测后的复盘,可一键提炼为<b style={{ color: 'var(--yin)' }}>新经验卡</b>,回到验证区重新验证 —— 闭环。</span>
          </div>

          {/* 溯源链 */}
          <ProvChain a={selA} onSel={setSel} onOpen={open} />
        </div>
      </div>
    </div>
  );
}

// 溯源链:来源 → 本体 → 衍生
function ProvChain({ a, onSel, onOpen }) {
  if (!a) return (
    <div style={{ marginTop: 22, border: '1px solid var(--line)', borderRadius: 12, background: 'var(--paper)', padding: '30px 22px', textAlign: 'center' }}>
      <div className="serif" style={{ fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.7 }}>点上方任意物料,这里展开它的<b style={{ color: 'var(--ink-2)' }}>溯源链</b> —— 它从哪条研报/因子而来,又衍生出哪些经验卡与席位。<br />任意节点可带上下文跳进对应模块。</div>
    </div>
  );
  const sources = (a.refs || []).map(id => GL.get(id)).filter(Boolean);
  const derived = GL.byRef(a.id);
  const Node = ({ x, big, dim }) => x ? (
    <div onClick={() => onSel(x.id)} className="gl-hover" style={{ cursor: 'pointer', border: '1px solid ' + (big ? 'var(--ink)' : 'var(--line)'), borderRadius: 10, background: big ? 'rgba(168,57,45,0.04)' : 'var(--paper)', padding: '11px 14px', minWidth: 150, opacity: dim ? 0.92 : 1, boxShadow: '0 1px 5px rgba(28,24,20,0.05)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: statusColor(x), flexShrink: 0 }} />
        <span className="mono" style={{ fontSize: 8, color: 'var(--paper)', background: 'var(--ink-2)', borderRadius: 4, padding: '1px 5px' }}>{TYPE_CN[x.type]}</span>
        {x.demo && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '0 4px' }}>示例</span>}
        {x.verdict && <span className="mono" style={{ fontSize: 8.5, color: statusColor(x) }}>{x.verdict}</span>}
      </div>
      <div className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 3 }}>{x.title}</div>
      {x.from && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{x.from}</div>}
      {x.expr && <code className="mono" style={{ display: 'block', fontSize: 9, color: 'var(--ink-2)', marginTop: 4, wordBreak: 'break-all' }}>{x.expr}</code>}
    </div>
  ) : null;
  return (
    <div style={{ marginTop: 22, border: '1px solid var(--line)', borderRadius: 12, background: 'var(--paper)', padding: '18px 22px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <span className="serif" style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>溯源链 · {a.title}</span>
        <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>来源 → 本体 → 衍生</span>
        <span onClick={() => onOpen(a)} className="serif" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 8, padding: '7px 15px', cursor: 'pointer' }}>带它跳进 {TYPE_CN[a.type]} 模块 →</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.1em' }}>来源 / 上游</span>
          {sources.length ? sources.map(s => <Node key={s.id} x={s} dim />) : <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>—(原始素材)</span>}
        </div>
        <span style={{ color: 'var(--ink-3)', fontSize: 18 }}>→</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span className="mono" style={{ fontSize: 9, color: 'var(--yin)', letterSpacing: '.1em' }}>本体</span>
          <Node x={a} big />
          {a.insight && <p className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.6, maxWidth: 230, margin: '2px 0 0', textWrap: 'pretty' }}>{a.insight}</p>}
        </div>
        <span style={{ color: 'var(--ink-3)', fontSize: 18 }}>→</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.1em' }}>衍生 / 下游</span>
          {derived.length ? derived.map(s => <Node key={s.id} x={s} dim />) : <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>—(暂无下游引用)</span>}
        </div>
      </div>
    </div>
  );
}

window.GraphApp = GraphApp;
