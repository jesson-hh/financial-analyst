// 观澜 · 落子 — 策略 · 校场(Foundry)· 文人书案(2026-07-11 三页重排:演武退役)
// 消费共享档案库(window.GL)的经验卡 / 因子 / 研报组装命名策略实例;验证归「复盘」页真跑 LLM run。
// 注:不重声明 React hooks(useState/useEffect 由 chart.jsx 全局提供);数据层(window.lzStrategy*/GL)不动。

const TCN = { card: '经验卡', factor: '因子', research: '研报' };
const TCOLOR = { card: 'var(--yin)', factor: 'var(--dai)', research: 'var(--jin)' };

// 料库回收站(软删除暂存):删卡先存这里 → 可「恢复」放回共享档案库,或「彻底删」永久移除。
// localStorage 持久(刷新不丢);恢复走 GL.put(同 id 同字段;research 类自动回推后端)。
const LZ_TRASH_KEY = 'guanlan:luozi:trash:v1';
function lzTrashLoad() { try { return JSON.parse(localStorage.getItem(LZ_TRASH_KEY)) || {}; } catch (e) { return {}; } }
function lzTrashSave(t) { try { localStorage.setItem(LZ_TRASH_KEY, JSON.stringify(t)); } catch (e) {} }

// 2026-07-11 三页重排:演武(scanSeat 价量启发式跨标的回测)退役——验证策略去「复盘」页真跑 LLM run;
//   校场只留:策略 CRUD(名/模板/信条/时钟/w/PA/绑票)+ 配方装配 + 料库(编辑/回收站)。

function Foundry() {
  const [tick, setTick] = useState(0);
  const [cur, setCur] = useState(null);
  const [editing, setEditing] = useState(null);   // null=不在编辑;对象=新建/改某策略草稿
  const [matTab, setMatTab] = useState('card');    // 料库分类页
  const [matEdit, setMatEdit] = useState(false);   // 料库编辑态:显形删除(从共享档案库移除,各页同步)
  const [delArm, setDelArm] = useState(null);      // 已点「删」待二次确认的物料 id(防误删)
  const [trashOpen, setTrashOpen] = useState(false);   // 回收站视图开关
  const [trash, setTrash] = useState(lzTrashLoad);     // 回收站内容(id→artifact+_trashedAt)
  useEffect(() => GL.on(() => setTick(t => t + 1)), []);

  // 候选 = 用户自命名的策略实例(第3期:席位 → 策略)
  const seats = window.lzStrategyList();
  const curId = cur || (seats[0] && seats[0].id);
  const seat = seats.find(s => s.id === curId) || null;
  const scol = (id) => window.lzStrategyColor ? window.lzStrategyColor(id) : 'var(--ink-2)';

  const refs = (seat && seat.refs || []).map(id => GL.get(id)).filter(Boolean);
  const refIds = new Set((seat && seat.refs) || []);
  const byType = (t) => refs.filter(r => r.type === t);

  const addRef = (id) => { if (seat && !refIds.has(id)) GL.link(seat.id, id); };
  const delRef = (id) => { if (seat) GL.patch(seat.id, { refs: (seat.refs || []).filter(r => r !== id) }); };

  // 回收站:trashItem 软删(存档→回收站)/ restoreItem 还原 / purgeItem 彻底删 / purgeAll 清空。
  const trashItem = (it) => {
    const t = lzTrashLoad();
    t[it.id] = Object.assign({}, GL.get(it.id) || it, { _trashedAt: Date.now() });
    lzTrashSave(t); setTrash(t);
    if (refIds.has(it.id)) delRef(it.id);   // 顺手从当前策略配方清掉(还原时不自动重连,需再拖入)
    GL.remove(it.id);                        // 从共享档案库移除(research 类同步 /archive/remove,防合并复活)
    setDelArm(null);
  };
  const restoreItem = (id) => {
    const t = lzTrashLoad(); const a = t[id]; if (!a) return;
    const art = Object.assign({}, a); delete art._trashedAt;
    GL.put(art);                             // 放回共享档案库(同 id 同字段;research 类自动回推后端)
    delete t[id]; lzTrashSave(t); setTrash(t);
  };
  const purgeItem = (id) => { const t = lzTrashLoad(); delete t[id]; lzTrashSave(t); setTrash(t); };
  const purgeAll = () => { lzTrashSave({}); setTrash({}); };
  const trashArr = Object.values(trash).sort((a, b) => (b._trashedAt || 0) - (a._trashedAt || 0));
  const trashN = trashArr.length;

  const newDraft = () => setEditing({ name: '', template: 'momentum', bind: [], clock: Object.assign({}, window.LZ_TEMPLATES.momentum.clock), refs: [], creed: window.LZ_TEMPLATES.momentum.creed, w: 0, pa: false, paMethod: '' });

  // 策略架:按名称序(演武排行退役,Sharpe 序无数据源)
  const board = React.useMemo(() => seats.map(s => ({ s }))
    .sort((x, y) => String(x.s.name || '').localeCompare(String(y.s.name || ''), 'zh')),
    [tick, seats.map(s => s.id).join()]);
  const matCount = GL.all('card').length + GL.all('factor').length + GL.all('research').length;

  // ── 策略架 roster 卡(演武成绩退役:验证去「复盘」页真跑)──
  const RosterCard = ({ s, i }) => {
    const on = s.id === curId && !editing;
    const bindNames = (s.bind || []).map(c => { const m = window.LZ_SYMBOL_META.find(x => x.code === c); return (m && m.name) || c; });
    return (
      <div onClick={() => { setEditing(null); setCur(s.id); }} className="hover-row"
        style={{ cursor: 'pointer', borderRadius: 11, padding: '11px 13px', transition: 'box-shadow .15s, border-color .15s',
          border: '1px solid ' + (on ? 'var(--ink)' : 'var(--line)'), background: on ? 'var(--paper-2)' : 'var(--paper)',
          boxShadow: on ? '0 3px 14px rgba(28,24,20,0.10)' : 'none', animation: 'fadeIn .3s ease both', animationDelay: (i * 0.035) + 's' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="serif" style={{ width: 22, height: 22, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 5, fontSize: 12, fontWeight: 600, color: on ? 'var(--paper)' : scol(s.id), background: on ? scol(s.id) : 'transparent', border: '1px solid ' + scol(s.id) }}>{s.glyph || '策'}</span>
          <span className="serif" style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
          {on && <span className="mono" style={{ fontSize: 8, color: 'var(--yin)', flexShrink: 0 }}>● 当前</span>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginTop: 8, flexWrap: 'wrap' }}>
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{(window.LZ_TEMPLATES[s.template] || {}).cn || s.template}</span>
          <span className="mono" title={bindNames.length ? ('盯盘票:' + bindNames.join('、')) : '未绑票 = 全局(每票可见,不入盯盘集)'}
            style={{ fontSize: 8, color: (s.bind && s.bind.length) ? 'var(--yin)' : 'var(--ink-3)', border: '1px solid ' + ((s.bind && s.bind.length) ? 'var(--zhu-soft)' : 'var(--line)'), borderRadius: 4, padding: '0 4px' }}>
            {(s.bind && s.bind.length) ? '盯 ' + s.bind.length + ' 票' : '全局'}
          </span>
          {(s.w || 0) > 0 && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>w={(+s.w).toFixed(2)}</span>}
          {s.pa && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>PA</span>}
        </div>
      </div>
    );
  };

  // ── 时钟刻度条 ──
  const ClockStrip = ({ clock }) => (
    <div style={{ display: 'flex', border: '1px solid var(--line)', borderRadius: 10, overflow: 'hidden', background: 'var(--paper)' }}>
      {[['止损', (clock.stopLoss * 100).toFixed(0) + '%', 'var(--dai)'], ['止盈', (clock.takeProfit * 100).toFixed(0) + '%', 'var(--zhu)'], ['最长持有', clock.maxHold + ' bar', 'var(--ink)'], ['镜头', clock.execTF, 'var(--ink)'], ['研判', clock.decisionFreq, 'var(--ink)']].map(([k, v, c], i) => (
        <div key={k} style={{ flex: 1, padding: '9px 12px', borderRight: i < 4 ? '1px solid var(--line-soft)' : 'none', background: i % 2 ? 'rgba(28,24,20,0.014)' : 'transparent' }}>
          <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '.12em' }}>{k}</div>
          <div className="mono" style={{ fontSize: 14, fontWeight: 600, color: c, marginTop: 3 }}>{v}</div>
        </div>
      ))}
    </div>
  );

  return (
    <div className="paper-bg" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
      {/* 顶栏 masthead */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, padding: '14px 28px 13px', borderBottom: '1px solid var(--line)', flexShrink: 0, background: 'linear-gradient(180deg, rgba(168,57,45,0.025), transparent 70%)' }}>
        <span className="seal serif" style={{ width: 30, height: 30, fontSize: 16, borderRadius: 3, alignSelf: 'center' }}>校</span>
        <span className="serif" style={{ fontSize: 20, fontWeight: 700, letterSpacing: '.05em', color: 'var(--ink)' }}>策略 · 校场</span>
        <span className="serif" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>遣 经验卡 · 因子 · 研报,自组命名策略;验证去「复盘」页真跑</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>共 <b style={{ color: 'var(--ink-2)' }}>{seats.length}</b> 策 · 料 <b style={{ color: 'var(--ink-2)' }}>{matCount}</b> 件</span>
      </div>

      <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
        {/* ── 左:策略架(候选+排行 合一)── */}
        <div style={{ width: 300, flexShrink: 0, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'linear-gradient(180deg, rgba(28,24,20,0.016), transparent 24%)' }}>
          <div style={{ padding: '15px 16px 9px', display: 'flex', alignItems: 'baseline', gap: 8, flexShrink: 0 }}>
            <span className="serif" style={{ fontSize: 13, fontWeight: 600, letterSpacing: '.14em', color: 'var(--ink)' }}>策 略 架</span>
            <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>按 名称序</span>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: '0 13px 14px', display: 'flex', flexDirection: 'column', gap: 9 }}>
            <div onClick={newDraft} className="serif" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, fontSize: 12.5, letterSpacing: '.16em', padding: '11px', borderRadius: 11, cursor: 'pointer', color: editing ? 'var(--paper)' : 'var(--yin)', background: editing ? 'var(--yin)' : 'transparent', border: '1px dashed ' + (editing ? 'var(--yin)' : 'var(--zhu-soft)'), transition: 'all .15s' }}>
              <span style={{ fontSize: 15, fontWeight: 300 }}>＋</span> 立 新 策
            </div>
            {board.map(({ s }, i) => <RosterCard key={s.id} s={s} i={i} />)}
            {!board.length && <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', padding: '20px 8px', lineHeight: 1.8 }}>架上无策<br />点上方「立新策」开始</div>}
          </div>
        </div>

        {/* ── 中:书案 ── */}
        <div onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); addRef(e.dataTransfer.getData('text/plain')); }}
          style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: '26px 34px 40px' }}>
          {editing ? (() => {
            const tpl = window.LZ_TEMPLATES[editing.template] || {};
            const setClock = (k, v) => setEditing(s => ({ ...s, clock: { ...s.clock, [k]: v } }));
            const lab = (t) => <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.16em', margin: '22px 0 9px' }}>{t}</div>;
            const numCell = (k, key, step) => (
              <label className="mono" style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                <span style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.06em' }}>{k}</span>
                <input type="number" step={step} value={editing.clock[key]} onChange={e => setClock(key, +e.target.value)}
                  style={{ width: 96, fontFamily: 'var(--mono)', fontSize: 15, fontWeight: 600, color: 'var(--ink)', background: 'transparent', border: 'none', borderBottom: '1.5px solid var(--line)', padding: '2px 2px 4px', outline: 'none' }} />
              </label>
            );
            const selCell = (k, key, opts) => (
              <label className="mono" style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                <span style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.06em' }}>{k}</span>
                <select value={editing.clock[key]} onChange={e => setClock(key, e.target.value)}
                  style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--ink)', background: 'transparent', border: 'none', borderBottom: '1.5px solid var(--line)', padding: '2px 2px 4px', outline: 'none', cursor: 'pointer' }}>
                  {opts.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
            );
            return (
              <div style={{ maxWidth: 720, animation: 'fadeIn .25s ease both' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span className="seal serif" style={{ width: 34, height: 34, fontSize: 17, borderRadius: 3, background: tpl.color || 'var(--yin)' }}>{tpl.glyph || '策'}</span>
                  <span className="serif" style={{ fontSize: 19, fontWeight: 700, color: 'var(--ink)' }}>{editing.id ? '改策略' : '立新策'}</span>
                </div>
                {lab('名 · 给这套打法起个名')}
                <input value={editing.name} onChange={e => setEditing(s => ({ ...s, name: e.target.value }))} placeholder="如「宁德·突破回踩」…" autoFocus
                  style={{ width: '100%', maxWidth: 460, fontFamily: 'var(--serif)', fontSize: 20, fontWeight: 600, color: 'var(--ink)', background: 'transparent', border: 'none', borderBottom: '2px solid var(--line)', padding: '4px 2px 8px', outline: 'none' }} />
                {lab('模 · 信号引擎(进场规则来自模板)')}
                <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
                  {window.LZ_TEMPLATE_IDS.map(t => {
                    const td = window.LZ_TEMPLATES[t], on = editing.template === t;
                    return (
                      <span key={t} onClick={() => setEditing(s => { const _td = window.LZ_TEMPLATES[t] || {}; return { ...s, template: t, creed: (!s.creed || s.creed === (window.LZ_TEMPLATES[s.template] || {}).creed) ? _td.creed : s.creed, clock: Object.assign({}, _td.clock, { execTF: s.clock.execTF }) }; })}
                        style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer', padding: '8px 14px', borderRadius: 10, border: '1px solid ' + (on ? td.color : 'var(--line)'), background: on ? td.color : 'var(--paper)', transition: 'all .15s' }}>
                        <span className="serif" style={{ fontSize: 13, color: on ? 'var(--paper)' : td.color }}>{td.glyph}</span>
                        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: on ? 'var(--paper)' : 'var(--ink-1)' }}>{td.cn}</span>
                      </span>
                    );
                  })}
                </div>
                <div className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 10, paddingLeft: 10, borderLeft: '2px solid ' + (tpl.color || 'var(--line)'), lineHeight: 1.5 }}>{tpl.creed}</div>
                <div style={{ marginTop: 10 }}>
                  <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '.18em' }}>信 条 · 喂给 agent 研判与条件单的交易哲学</div>
                  <textarea value={editing.creed || ''} rows={2}
                    onChange={e => setEditing(s => ({ ...s, creed: e.target.value }))}
                    placeholder={(window.LZ_TEMPLATES[editing.template] || {}).creed || ''}
                    style={{ width: '100%', background: 'transparent', border: 'none', borderBottom: '1px solid var(--line)', color: 'var(--ink)', fontSize: 12, fontFamily: 'inherit', resize: 'vertical', outline: 'none', padding: '4px 0' }} />
                </div>
                {/* 价格行为研判:开关 + 可编辑方法论(几何始终算/显;开关只控制是否注入 LLM 研判 prompt)*/}
                <div style={{ margin: '22px 0 8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.16em' }}>价格行为</span>
                    <span onClick={() => setEditing(s => ({ ...s, pa: !s.pa, paMethod: (!s.pa && !s.paMethod) ? (window.LZ_PA_METHOD_DEFAULT || '') : s.paMethod }))}
                      className="serif"
                      style={{ fontSize: 11.5, cursor: 'pointer', padding: '4px 12px', borderRadius: 8, transition: 'all .12s',
                        border: '1px solid ' + (editing.pa ? 'var(--ink)' : 'var(--line)'),
                        background: editing.pa ? 'var(--ink)' : 'var(--paper)', color: editing.pa ? 'var(--paper)' : 'var(--ink-2)' }}>
                      {editing.pa ? '✓ 注入几何 + 方法论' : '关 · 点击开启'}
                    </span>
                    <span className="serif" style={{ fontSize: 10, color: 'var(--ink-3)' }}>几何特征始终计算并在决策卡显示;开关只控制是否注入 LLM 研判 prompt</span>
                  </div>
                  {editing.pa && (
                    <textarea value={editing.paMethod} onChange={e => setEditing(s => ({ ...s, paMethod: e.target.value }))}
                      placeholder={window.LZ_PA_METHOD_DEFAULT || ''} rows={7}
                      style={{ width: '100%', marginTop: 8, fontFamily: 'var(--serif)', fontSize: 11.5, lineHeight: 1.65,
                        color: 'var(--ink)', background: 'transparent', border: '1px solid var(--line)', borderRadius: 8,
                        padding: '8px 10px', outline: 'none', resize: 'vertical', boxSizing: 'border-box' }} />
                  )}
                </div>
                {lab('钟 · 交易时钟(止损止盈到价 · 最长持有时间止盈)')}
                <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                  {numCell('止损', 'stopLoss', '0.01')}
                  {numCell('止盈', 'takeProfit', '0.01')}
                  {numCell('最长持有 (bar)', 'maxHold', '1')}
                  {selCell('看盘镜头', 'execTF', ['day', '5min'])}
                  {selCell('研判频率', 'decisionFreq', ['hourly', 'daily'])}
                </div>
                {/* P3:因子权重 w —— 进信号的混合系数。w 是策略级顶层字段(非 clock),独立控件。 */}
                {lab('权 · 因子进信号(混合权重 w)')}
                <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
                  <input type="range" min="0" max="1" step="0.05"
                    value={(editing.w != null ? editing.w : 0)}
                    onChange={e => setEditing(s => ({ ...s, w: +e.target.value }))}
                    style={{ width: 240, accentColor: 'var(--yin)', cursor: 'pointer' }} />
                  <span className="mono" style={{ fontSize: 16, fontWeight: 700, color: (editing.w > 0 ? 'var(--yin)' : 'var(--ink-3)'), minWidth: 48 }}>w={(+(editing.w != null ? editing.w : 0)).toFixed(2)}</span>
                </div>
                <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.55 }}>0=纯LLM(方向只由 agent 研判定);&gt;0 时按 (1−w)·LLM分 + w·vintage 因子 z 分混入决策方向(因子用 as-of 真 OOS,不看未来)。</div>
                {lab('绑 · 这套策略管哪些票')}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                  {window.LZ_SYMBOL_META.map(m => {
                    const on = editing.bind.includes(m.code);
                    return (
                      <span key={m.code} onClick={() => setEditing(s => ({ ...s, bind: s.bind.includes(m.code) ? s.bind.filter(x => x !== m.code) : [...s.bind, m.code] }))}
                        className="serif" style={{ fontSize: 11.5, cursor: 'pointer', padding: '5px 11px', borderRadius: 8, border: '1px solid ' + (on ? 'var(--ink)' : 'var(--line)'), background: on ? 'var(--ink)' : 'var(--paper)', color: on ? 'var(--paper)' : 'var(--ink-2)', transition: 'all .12s' }}>{m.name}</span>
                    );
                  })}
                </div>
                <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 8 }}>不绑 = 全局(每只票都可见)</div>
                <div style={{ display: 'flex', gap: 11, marginTop: 28 }}>
                  <span onClick={() => { const id = window.lzStrategySave({ id: editing.id, name: editing.name, template: editing.template, clock: editing.clock, bind: editing.bind, refs: editing.id ? (window.lzStrategyGet(editing.id) || {}).refs : [], creed: editing.creed, w: editing.w, pa: editing.pa, paMethod: editing.paMethod }); setEditing(null); setCur(id); }}
                    className="serif" style={{ fontSize: 13.5, letterSpacing: '.1em', color: 'var(--paper)', background: 'var(--yin)', borderRadius: 10, padding: '10px 26px', cursor: 'pointer', boxShadow: '0 3px 14px rgba(168,57,45,0.22)' }}>钤 印 · 保存</span>
                  <span onClick={() => setEditing(null)} className="serif" style={{ fontSize: 13, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 10, padding: '10px 22px', cursor: 'pointer' }}>取消</span>
                </div>
              </div>
            );
          })() : seat ? (() => {
            const tcn = (window.LZ_TEMPLATES[seat.template] || {}).cn || seat.template;
            const creed = seat.creed || (window.LZ_TEMPLATES[seat.template] || {}).creed;
            const mgmt = (c, on) => ({ fontSize: 10, padding: '4px 11px', borderRadius: 8, cursor: 'pointer', color: c, border: '1px solid ' + (on || 'var(--line)') });
            return (
              <div style={{ maxWidth: 760, animation: 'fadeIn .25s ease both' }}>
                {/* 策略帖 抬头 */}
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
                  <span className="seal serif" style={{ width: 42, height: 42, fontSize: 21, borderRadius: 3, background: scol(seat.id), flexShrink: 0 }}>{seat.glyph || '策'}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="serif" style={{ fontSize: 23, fontWeight: 700, color: 'var(--ink)', lineHeight: 1.15 }}>{seat.name}</div>
                    <div className="serif" style={{ fontSize: 12.5, color: 'var(--ink-2)', marginTop: 4 }}><b style={{ color: scol(seat.id) }}>{tcn}</b> · {creed}</div>
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    <span onClick={() => setEditing(Object.assign({}, seat, { clock: Object.assign({}, seat.clock), bind: (seat.bind || []).slice(), pa: !!seat.pa, paMethod: seat.paMethod || '' }))} className="mono" style={mgmt('var(--ink-2)')}>编辑</span>
                    <span onClick={() => { const id = window.lzStrategySave(Object.assign({}, seat, { id: undefined, name: seat.name + ' 副本' })); setCur(id); }} className="mono" style={mgmt('var(--ink-2)')}>复制</span>
                    <span onClick={() => { window.lzStrategyDelete(seat.id); setCur(null); }} className="mono" style={mgmt('var(--dai)', 'var(--dai-soft)')}>删除</span>
                  </div>
                </div>

                <div style={{ height: 1, background: 'linear-gradient(90deg, var(--jin), transparent 55%)', margin: '18px 0 0', opacity: 0.5 }} />

                {/* 时钟 */}
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.16em', margin: '20px 0 9px' }}>交 易 时 钟</div>
                <ClockStrip clock={seat.clock} />

                {/* 配方 */}
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, margin: '24px 0 10px' }}>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.16em' }}>配 方</span>
                  <span className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{refs.length} 件料 · 依据展示并喂 LLM 研判,不参与确定性回测</span>
                  <span style={{ flex: 1 }} />
                  <span className="serif" style={{ fontSize: 10, color: 'var(--ink-3)' }}>← 从右侧料库 拖入 / 点 ＋</span>
                </div>
                {['card', 'factor', 'research'].map(t => {
                  const items = byType(t);
                  return (
                    <div key={t} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 9 }}>
                      <span className="mono" style={{ fontSize: 9, color: TCOLOR[t], width: 40, flexShrink: 0, paddingTop: 6 }}>{TCN[t]}</span>
                      <div style={{ flex: 1, display: 'flex', flexWrap: 'wrap', gap: 7, minHeight: 26 }}>
                        {items.length ? items.map(r => (
                          <span key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 7, border: '1px solid ' + TCOLOR[t], borderRadius: 8, background: 'var(--paper)', padding: '5px 10px' }}>
                            <span className="serif" style={{ fontSize: 11.5, color: 'var(--ink)' }}>{r.title}</span>
                            {r.demo && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '0 4px' }}>示例</span>}
                            {r.expr && <code className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{r.expr.slice(0, 16)}{r.expr.length > 16 ? '…' : ''}</code>}
                            <span onClick={() => delRef(r.id)} style={{ cursor: 'pointer', color: 'var(--ink-3)', fontSize: 13, lineHeight: 1 }}>×</span>
                          </span>
                        )) : <span className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)', paddingTop: 5 }}>—</span>}
                      </div>
                    </div>
                  );
                })}

                {/* 验证入口(演武退役):真跑 LLM run 归「复盘」页 */}
                <div className="serif" style={{ marginTop: 26, fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.7, padding: '10px 14px', border: '1px dashed var(--line)', borderRadius: 10 }}>
                  要验证这套策略?去顶栏「<b style={{ color: 'var(--yin)' }}>复盘</b>」页,选它 + 选区间,一键真跑(逐 bar 真 LLM 研判,run 化留档)。旧「演武」为价量启发式非 LLM,已退役。
                </div>
              </div>
            );
          })() : (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--ink-3)', gap: 14 }}>
              <span className="seal serif" style={{ width: 48, height: 48, fontSize: 24, borderRadius: 4, opacity: 0.4 }}>策</span>
              <div className="serif" style={{ fontSize: 14, color: 'var(--ink-2)' }}>书案空置</div>
              <div onClick={newDraft} className="serif" style={{ fontSize: 12.5, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 9, padding: '9px 20px', cursor: 'pointer' }}>＋ 立第一套策略</div>
            </div>
          )}
        </div>

        {/* ── 右:料库 ── */}
        <div style={{ width: 460, flexShrink: 0, borderLeft: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'rgba(28,24,20,0.02)' }}>
          <div style={{ padding: '14px 15px 0', flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, letterSpacing: '.14em', color: 'var(--ink)' }}>{trashOpen ? '回收站' : '料 库'}</span>
              <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{trashOpen ? (trashN + ' 件可恢复') : '共享档案库'}</span>
              <span style={{ flex: 1 }} />
              {!trashOpen && <span onClick={() => { setMatEdit(e => !e); setDelArm(null); }} className="mono" title={matEdit ? '退出编辑' : '编辑料库 · 删除不用的卡 / 研报 / 因子(进回收站,可恢复)'} style={{ fontSize: 9, padding: '3px 9px', borderRadius: 7, cursor: 'pointer', color: matEdit ? 'var(--paper)' : 'var(--ink-2)', background: matEdit ? 'var(--dai)' : 'transparent', border: '1px solid ' + (matEdit ? 'var(--dai)' : 'var(--line)') }}>{matEdit ? '完成' : '编辑'}</span>}
              <span onClick={() => { setTrashOpen(o => !o); setDelArm(null); }} className="mono" title="回收站:恢复或彻底删除已删物料" style={{ fontSize: 9, padding: '3px 9px', borderRadius: 7, cursor: 'pointer', color: trashOpen ? 'var(--ink-2)' : 'var(--ink-3)', background: 'transparent', border: '1px solid ' + (trashOpen ? 'var(--ink-2)' : 'var(--line)') }}>{trashOpen ? '← 料库' : ('回收站' + (trashN ? ' ' + trashN : ''))}</span>
            </div>
            <div className="serif" style={{ fontSize: 9.5, color: (matEdit && !trashOpen) ? 'var(--dai)' : 'var(--ink-3)', marginTop: 3 }}>{trashOpen ? '已删物料暂存此处 · 点「恢复」放回料库,或「彻底删」永久移除' : (matEdit ? '点卡片「删」移入回收站(可恢复;不彻底删)' : (seat ? '拖到书案 / 点 ＋ 入「' + seat.name + '」配方' : '先在左侧选一套策略,再配料'))}</div>
            {!trashOpen && <div style={{ display: 'flex', gap: 6, marginTop: 11 }}>
              {['card', 'research', 'factor'].map(t => {
                const on = matTab === t;
                return (
                  <span key={t} onClick={() => { setMatTab(t); setDelArm(null); }} className="mono" style={{ fontSize: 9.5, padding: '4px 9px', borderRadius: 7, cursor: 'pointer', whiteSpace: 'nowrap', color: on ? 'var(--paper)' : TCOLOR[t], background: on ? TCOLOR[t] : 'transparent', border: '1px solid ' + (on ? TCOLOR[t] : 'var(--line)') }}>{TCN[t]} {GL.all(t).length}</span>
                );
              })}
            </div>}
          </div>
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: '11px 13px' }}>
            {trashOpen ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {trashN === 0 && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', padding: '12px 2px', lineHeight: 1.6 }}>回收站是空的 —— 在料库「编辑」态点「删」的卡 / 研报 / 因子会进这里,可恢复。</div>}
                {trashN > 0 && <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 2 }}><span onClick={purgeAll} className="mono" title="永久清空回收站(不可恢复)" style={{ fontSize: 9, color: 'var(--ink-3)', cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 6, padding: '3px 9px' }}>清空回收站</span></div>}
                {trashArr.map(it => (
                  <div key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 10, border: '1px solid var(--line)', borderRadius: 10, background: 'var(--paper)', padding: '10px 13px' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.title || it.id}</div>
                      <div style={{ display: 'flex', gap: 7, marginTop: 3, alignItems: 'center' }}>
                        <span className="mono" style={{ fontSize: 8.5, color: TCOLOR[it.type] || 'var(--ink-3)' }}>{TCN[it.type] || it.type}</span>
                        {it.demo && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '0 4px' }}>示例</span>}
                      </div>
                    </div>
                    <span onClick={() => restoreItem(it.id)} className="mono" title="放回料库(共享档案库)" style={{ fontSize: 9, padding: '3px 9px', borderRadius: 6, cursor: 'pointer', color: 'var(--yin)', border: '1px solid var(--zhu-soft)', flexShrink: 0 }}>恢复</span>
                    <span onClick={() => purgeItem(it.id)} className="mono" title="永久删除(不可恢复)" style={{ fontSize: 9, padding: '3px 8px', borderRadius: 6, cursor: 'pointer', color: 'var(--ink-3)', border: '1px solid var(--line)', flexShrink: 0 }}>彻底删</span>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 9, alignContent: 'start' }}>
            {GL.all(matTab).map(it => {
              const used = refIds.has(it.id);
              return (
                <div key={it.id} draggable={!matEdit} onDragStart={e => { if (!matEdit) e.dataTransfer.setData('text/plain', it.id); }} onClick={() => { if (!matEdit) addRef(it.id); }} title={it.insight || it.expr || it.from || ''}
                  style={{ display: 'flex', alignItems: 'center', gap: 10, border: '1px solid ' + ((used && !matEdit) ? TCOLOR[matTab] : (delArm === it.id ? 'var(--dai)' : 'var(--line)')), borderRadius: 10, background: (used && !matEdit) ? 'var(--paper-2)' : 'var(--paper)', padding: '11px 14px', cursor: matEdit ? 'default' : (used ? 'default' : (seat ? 'grab' : 'default')), opacity: (used && !matEdit) ? 0.58 : 1, transition: 'border-color .12s, box-shadow .12s' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)', lineHeight: 1.35 }}>{it.title}</div>
                    {it.insight && <div className="serif" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 3, lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{it.insight}</div>}
                    <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                      {it.demo && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '0 4px', flexShrink: 0 }}>示例</span>}
                      {it.ic && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>IC {it.ic}</span>}
                      {it.verdict && <span className="mono" style={{ fontSize: 8.5, color: TCOLOR[matTab] }}>{it.verdict}</span>}
                      {(it.from || it.kind) && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.from || it.kind}</span>}
                    </div>
                  </div>
                  {matEdit ? (
                    delArm === it.id ? (
                      <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                        <span onClick={e => { e.stopPropagation(); trashItem(it); }} className="mono" title="移入回收站(可在「回收站」恢复)" style={{ fontSize: 9, padding: '3px 7px', borderRadius: 6, cursor: 'pointer', color: 'var(--paper)', background: 'var(--dai)', border: '1px solid var(--dai)' }}>确认删</span>
                        <span onClick={e => { e.stopPropagation(); setDelArm(null); }} className="mono" title="取消" style={{ fontSize: 12, color: 'var(--ink-3)', cursor: 'pointer' }}>×</span>
                      </span>
                    ) : (
                      <span onClick={e => { e.stopPropagation(); setDelArm(it.id); }} className="mono" title="移入回收站(可恢复)" style={{ fontSize: 9, padding: '3px 8px', borderRadius: 6, cursor: 'pointer', color: 'var(--dai)', border: '1px solid var(--line)', flexShrink: 0 }}>删</span>
                    )
                  ) : (
                    <span className="mono" style={{ fontSize: used ? 10 : 15, color: used ? TCOLOR[matTab] : 'var(--ink-3)', flexShrink: 0 }}>{used ? '✓' : '＋'}</span>
                  )}
                </div>
              );
            })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

window.Foundry = Foundry;
