// 观澜 · 选股 — 主壳 (因子 → 今日候选清单 → 约束 → 组合 → 落子)
const { useState, useMemo, useEffect, useRef } = React;

const fmtPct = (x, d = 1) => (x >= 0 ? '+' : '') + (x * 100).toFixed(d) + '%';
const wPct = (x) => (x * 100).toFixed(1) + '%';
const upc = (x) => (x >= 0 ? 'var(--zhu)' : 'var(--dai)');
const TODAY = new Date().toISOString().slice(0, 10);  // 仅离线玩具路径显示(v4 真路径用后端 result.date);原硬编 2026-06-04 会过期(审计 M6)
// 选股接缝已翻开(互通审计 P1⑨):「据此落子」写共享决策 + handoff('cockpit')(落子页已接收消费),
// take('screen') 收外部因子按全目录解析。要回到孤岛态,翻回 true 即可(可逆接缝)。
const ISOLATED = false;
// 帷幄融合旗:EMBED=被帷幄嵌入(隐藏页头身份区);LEGACY=找回页内 agent 窗口(默认全局隐藏,spec §3.7)
const WW_EMBED = new URLSearchParams(location.search).get('embed') === '1';
const WW_LEGACY = new URLSearchParams(location.search).get('legacy') === '1';
// 帷幄会话工作台隔离:带 ?ws=<会话id>(嵌入或从工作台 ↗ 独立打开)→ handoff 信箱按会话取,防 A/B 会话串扰;无 ws = 裸键如旧
const WW_WS = new URLSearchParams(location.search).get('ws') || '';

// ───────── LLM 选因子 (列出因子 + 建议权重 + 理由) ─────────
const FACTOR_REASON = {
  fa_reversal: '震荡/超跌环境下缩量企稳反转弹性大,作为主 alpha 来源。',
  fa_north: '北向资金回流确认资金面,过滤纯技术噪声、提高胜率。',
  fa_pead: '财报季业绩超预期个股有约 60 日漂移,补一层基本面。',
  fa_distrib: '高位放量滞涨易退潮,作为风控惩罚层对尾部降权。',
};
function llmPick(prompt) {
  const t = prompt || ''; const out = []; const add = (id, w) => { if (!out.find(f => f.id === id)) out.push({ id, w }); };
  if (/超跌|反弹|反转|缩量|震荡|低吸/.test(t)) add('fa_reversal', 1);
  if (/资金|北向|外资|蓝筹|主力/.test(t)) add('fa_north', 0.7);
  if (/业绩|基本面|财报|超预期|成长|景气/.test(t)) add('fa_pead', 0.8);
  if (/风控|回撤|防守|退潮|稳|低波/.test(t)) add('fa_distrib', 0.6);
  if (out.length === 0) { add('fa_reversal', 1); add('fa_distrib', 0.6); } // 默认组合(北向/PEAD 已除名:北向停披/PEAD无表达式)
  const reasons = {}; out.forEach(f => { reasons[f.id] = FACTOR_REASON[f.id]; });
  const summary = '已拆出 ' + out.length + ' 个因子并给出建议权重,可在下方逐项微调:' + out.map(f => (window.XG_FBYID[f.id] || { short: f.id }).short).join(' · ');
  return { factors: out, reasons, summary };
}

// ───────── 默认配置 ─────────
function defaultCfg() {
  const pick = llmPick('');
  return {
    factors: pick.factors,
    reasons: pick.reasons,
    llmSummary: pick.summary,
    topN: 20,
    blend: 1.0,
    pool: 'all',
    model: 'prod',
    mlStatus: ['mainline', 'initiation', 'revival', 'decay', 'cold', 'neutral'],  // 全选=不筛
    industryNeutral: true,
    indCap: 0.25,
    liqMin: 5,
    exclST: true, exclHalt: true, exclLimit: true, exclNew: false,
  };
}

// ───────── 一句话 → 约束 (轻解析) ─────────
function parsePhrase(q, cfg) {
  const t = q || ''; const c = JSON.parse(JSON.stringify(cfg)); const hit = [];
  const ensure = (id, w) => { if (!c.factors.find(f => f.id === id)) { c.factors.push({ id, w: w || 0.8 }); } };
  const drop = (id) => { c.factors = c.factors.filter(f => f.id !== id); };
  if (/集中|精选|少而精|高信念/.test(t)) { c.topN = 12; hit.push('更集中 · 12 只'); }
  if (/分散|宽基|多一些|铺开/.test(t)) { c.topN = 35; hit.push('更分散 · 35 只'); }
  if (/均衡|中性|别扎堆|行业/.test(t)) { c.industryNeutral = true; c.indCap = 0.2; hit.push('行业中性 · 单业≤20%'); }
  if (/蓝筹|大盘|价值|龙头/.test(t)) { ensure('fa_north', 0.8); hit.push('纳入北向动量'); }
  if (/成长|弹性|进攻/.test(t)) { ensure('fa_pead', 0.9); ensure('fa_reversal', 1); hit.push('偏成长 · 反转+业绩'); }
  if (/稳|低波|防守|控回撤|风控/.test(t)) { ensure('fa_distrib', 0.7); c.exclLimit = true; hit.push('叠加退潮风控层'); }
  if (/业绩|基本面|超预期/.test(t)) { ensure('fa_pead', 1); hit.push('纳入 PEAD 漂移'); }
  if (/北向|外资|聪明钱/.test(t)) { ensure('fa_north', 1); hit.push('纳入北向动量'); }
  if (/反转|超跌|缩量/.test(t)) { ensure('fa_reversal', 1); hit.push('纳入缩量反转'); }
  if (/剔除次新|去次新/.test(t)) { c.exclNew = true; hit.push('剔除次新'); }
  if (/等权/.test(t)) { c.weighting = 'equal'; hit.push('等权配置'); }
  return { cfg: c, hit };
}

// ───────── v4 模型工坊(右侧抽屉:选因子 → 训练命名变体 → 看进度 → 管理变体)─────────
function ModelWorkshop({ API, models, reloadModels, flash, onPick, onClose }) {
  const [baseFeats, setBaseFeats] = useState([]);
  const [baseNote, setBaseNote] = useState('');
  const [selBase, setSelBase] = useState(() => new Set());
  const [selLib, setSelLib] = useState(() => new Set());
  const [name, setName] = useState('');
  const [train, setTrain] = useState({ busy: false, phase: 'idle', label: '', step: 0, total: 3, elapsed: 0 });
  const _poll = useRef(null);
  const say = flash || ((t, b) => { try { console.log('[工坊]', t, b); } catch (e) {} });

  // 挂载拉 v4 基础特征 → 默认全选
  useEffect(() => {
    if (!API || !window.xgBaseFeatures) { setBaseNote('需连接 9999 后端'); return; }
    window.xgBaseFeatures(API).then(j => {
      const fs = (j && j.ok && Array.isArray(j.features)) ? j.features : [];
      setBaseFeats(fs);
      setSelBase(new Set(fs));   // 默认全选
      if (!fs.length) setBaseNote('未取到基础特征');
    }).catch(() => setBaseNote('基础特征拉取失败'));
    return () => { if (_poll.current) { clearInterval(_poll.current); _poll.current = null; } };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const libFactors = (window.XG_FACTORS || []).filter(f => f && f.id);
  const nSel = selBase.size + selLib.size;

  const toggleSet = (setter, key) => setter(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });
  const allBase = baseFeats.length > 0 && selBase.size === baseFeats.length;
  const toggleAllBase = () => setSelBase(allBase ? new Set() : new Set(baseFeats));

  const doTrain = async () => {
    if (!API || train.busy) return;
    const spec = { name: name || '未命名变体', factor_ids: [...selLib], base_features: [...selBase], universe: 'all' };
    let r;
    try { r = await window.xgTrain(API, spec); } catch (e) { say('训练未启动', String((e && e.message) || e)); return; }
    if (!r || !r.ok) { say('训练未启动', (r && r.reason) || '失败'); return; }
    const vid = r.variant_id;   // 训完按此 id 找回变体,核其 unsupported_factors → 诚实告知用户哪些因子被丢弃
    setTrain({ busy: true, phase: 'starting', label: '启动训练子进程…', step: 0, total: 3, elapsed: 0 });
    if (_poll.current) clearInterval(_poll.current);
    _poll.current = setInterval(async () => {
      let s = {};
      try { s = (await window.xgTrainStatus(API)).state || {}; } catch (e) { return; }
      setTrain({ busy: !!s.running, phase: s.phase, label: s.label, step: s.step, total: s.total || 3, elapsed: s.elapsed_sec || 0 });
      if (!s.running) {
        clearInterval(_poll.current); _poll.current = null;
        reloadModels();
        if (s.ok) {
          try {
            const j = await window.xgModels(API);
            const v = ((j && j.variants) || []).find(x => x.id === vid);
            const unsup = (v && v.unsupported_factors) || [];
            say(unsup.length ? '变体已训好(部分因子未用)' : '变体已训好',
                unsup.length ? ('⚠ ' + unsup.length + ' 个因子无法求值已忽略:' + unsup.join(', ') + ' · 该变体未用这些因子') : 'OOS 见列表');
          } catch (e) { say('变体已训好', 'OOS 见列表'); }
        } else {
          say('训练失败', s.error || '');
        }
      }
    }, 2500);
  };

  const variants = (models || []).filter(m => m && m.id !== 'prod');
  const delVariant = async (id, nm) => {
    if (!window.confirm('删除变体 ' + (nm || id) + '?')) return;
    try { await window.xgDeleteModel(API, id); } catch (e) {}
    reloadModels();
  };

  // 快验/严格验证(CPCV)
  const _valPoll = useRef(null);
  const runValidate = async (id, tier) => {
    if (!API) { say('验证', '需连接 9999 后端'); return; }
    let r;
    try {
      const resp = await fetch(API + '/screen/model/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, tier }) });
      r = await resp.json();
    } catch (e) { say(tier === 'quick' ? '快验' : '严格验证', '请求失败'); return; }
    if (tier === 'quick') {
      if (r && r.ok) say('快验', 'DSR ' + (r.result && r.result.dsr != null ? r.result.dsr : '—') + ' · 夏普 ' + (r.result && r.result.sharpe != null ? r.result.sharpe : '—') + (r.result && !r.result.ready ? '(证据不足)' : ''));
      else say('快验', '失败');
      return;
    }
    if (!r || !r.ok) { say('严格验证', (r && r.reason) || '启动失败'); return; }
    say('严格验证', '已起(~分钟级),完成回灌');
    if (_valPoll.current) clearInterval(_valPoll.current);
    _valPoll.current = setInterval(async () => {
      let s = {};
      try { const sr = await fetch(API + '/screen/model/validate/status'); s = ((await sr.json()).state) || {}; } catch (e) { return; }
      if (!s.running && s.phase === 'done') {
        clearInterval(_valPoll.current); _valPoll.current = null;
        say('严格验证', s.ok ? ('完成 DSR ' + (s.result && s.result.dsr != null ? s.result.dsr : '—') + ' · 夏普中位 ' + (s.result && s.result.sharpe_dist && s.result.sharpe_dist.median != null ? s.result.sharpe_dist.median : '—')) : ('失败:' + (s.error || '')));
      }
    }, 4000);
  };

  const cbStyle = { display: 'flex', alignItems: 'center', gap: 7, padding: '3px 4px', cursor: 'pointer', borderRadius: 5, fontSize: 11.5 };
  const grpLabel = { fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)', marginBottom: 8, fontFamily: 'var(--mono)' };

  return (
    <div style={{ position: 'fixed', top: 0, right: 0, bottom: 0, width: 420, zIndex: 60, background: 'var(--paper)', borderLeft: '1px solid var(--line)', overflow: 'auto', boxShadow: '-8px 0 28px rgba(28,24,20,0.18)' }}>
      {/* 头部 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '13px 16px', borderBottom: '1px solid var(--line)', position: 'sticky', top: 0, background: 'var(--paper)', zIndex: 2 }}>
        <span className="seal" style={{ width: 22, height: 22, fontSize: 12, borderRadius: 6 }}>瀾</span>
        <span className="serif" style={{ fontSize: 14, fontWeight: 600, letterSpacing: '0.03em', flex: 1 }}>⚙ 模型工坊</span>
        <span onClick={onClose} title="关闭" className="mono" style={{ fontSize: 14, color: 'var(--ink-3)', cursor: 'pointer', padding: '2px 6px' }}>✕</span>
      </div>
      <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)', padding: '7px 16px 0', lineHeight: 1.5 }}>训练 v4 变体 · 不动生产模型</div>

      {/* 命名 + 训练 */}
      <div style={{ padding: '11px 16px', display: 'flex', gap: 8, alignItems: 'center' }}>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="变体名,如 估值实验" className="serif"
          style={{ flex: 1, boxSizing: 'border-box', border: '1px solid var(--line)', borderRadius: 7, padding: '7px 10px', fontSize: 12, color: 'var(--ink)', background: 'var(--paper)', outline: 'none' }} />
        <span onClick={(nSel === 0 || train.busy) ? undefined : doTrain} className="serif"
          title={nSel === 0 ? '至少选 1 个因子' : (train.busy ? '训练中…' : '训练 v4 变体')}
          style={{ flexShrink: 0, fontSize: 12.5, color: 'var(--paper)', background: (nSel === 0 || train.busy) ? 'var(--ink-3)' : 'var(--yin)', borderRadius: 7, padding: '7px 14px', cursor: (nSel === 0 || train.busy) ? 'not-allowed' : 'pointer', opacity: (nSel === 0 || train.busy) ? 0.6 : 1 }}>
          🔨 训练
        </span>
      </div>

      {/* 训练进度 */}
      {train.busy && (
        <div style={{ margin: '0 16px 11px', padding: '9px 11px', border: '1px solid var(--zhu-soft)', borderRadius: 8, background: 'rgba(168,57,45,0.05)' }}>
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--yin)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ animation: 'pulse 1s infinite' }}>⟲</span>
            {(train.label || '训练中') + ' (' + (train.step || 0) + '/' + (train.total || 3) + (train.elapsed ? ' · ' + train.elapsed + 's' : '') + ')'}
          </div>
          <div style={{ height: 5, background: 'rgba(28,24,20,0.07)', borderRadius: 3, overflow: 'hidden', marginTop: 7 }}>
            <div style={{ width: Math.min(100, Math.round((train.step || 0) / (train.total || 3) * 100)) + '%', height: '100%', background: 'var(--yin)', opacity: 0.8, transition: 'width .3s' }} />
          </div>
        </div>
      )}

      {/* v4 基础特征 */}
      <div style={{ padding: '11px 16px', borderTop: '1px solid var(--line-soft)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
          <span style={grpLabel}>〈v4 基础特征〉{baseFeats.length ? ' · ' + selBase.size + '/' + baseFeats.length : ''}</span>
          {baseFeats.length > 0 && (
            <span onClick={toggleAllBase} className="mono" style={{ fontSize: 9.5, color: 'var(--dai)', cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 12, padding: '2px 9px' }}>{allBase ? '全不选' : '全选'}</span>
          )}
        </div>
        {baseFeats.length === 0 && <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{baseNote || '加载中…'}</div>}
        {baseFeats.map(f => (
          <label key={f} className="hover-row" style={cbStyle} onClick={() => toggleSet(setSelBase, f)}>
            <input type="checkbox" readOnly checked={selBase.has(f)} style={{ accentColor: 'var(--dai)', cursor: 'pointer' }} />
            <span className="mono" style={{ color: 'var(--ink-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f}</span>
          </label>
        ))}
      </div>

      {/* 我的因子库 */}
      <div style={{ padding: '11px 16px', borderTop: '1px solid var(--line-soft)' }}>
        <div style={grpLabel}>〈我的因子库〉{libFactors.length ? ' · ' + selLib.size + ' 选中' : ''}</div>
        {libFactors.length < 5 && <div className="serif" style={{ fontSize: 10, color: 'var(--ink-3)', marginBottom: 6 }}>因子目录加载中,稍候(从 /screen/factors 拉取)…</div>}
        {libFactors.map(f => (
          <label key={f.id} className="hover-row" style={cbStyle} onClick={() => toggleSet(setSelLib, f.id)} title={f.desc || ''}>
            <input type="checkbox" readOnly checked={selLib.has(f.id)} style={{ accentColor: 'var(--yin)', cursor: 'pointer' }} />
            <span className="serif" style={{ color: 'var(--ink-1)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.short || f.id}</span>
            {f.ic != null && <span className="mono" style={{ fontSize: 9, color: f.ic >= 0 ? 'var(--zhu)' : 'var(--dai)', flexShrink: 0 }}>IC {(f.ic >= 0 ? '+' : '') + (+f.ic).toFixed(3)}</span>}
          </label>
        ))}
      </div>

      {/* 已训变体 */}
      <div style={{ padding: '11px 16px', borderTop: '1px solid var(--line-soft)' }}>
        <div style={grpLabel}>〈已训变体〉{variants.length ? ' · ' + variants.length : ''}</div>
        {variants.length === 0 && <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>暂无变体 · 选因子后「🔨 训练」生成。</div>}
        {variants.map(m => (
          <div key={m.id} className="hover-row" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 6px', borderRadius: 6, borderBottom: '1px solid var(--line-soft)' }}>
            <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={() => { onPick && onPick(m.id); onClose && onClose(); }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{m.name || m.id}</span>
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--paper)', background: 'var(--ink-3)', borderRadius: 4, padding: '0 4px', flexShrink: 0 }}>{m.source === 'workflow' ? '来自工作流' : '本工坊'}</span>
                {m.kind && m.kind !== 'v4-lgb' && <span className="mono" style={{ fontSize: 8.5, color: 'var(--paper)', background: 'var(--ink-3)', borderRadius: 4, padding: '0 4px', flexShrink: 0 }}>{m.kind}</span>}
                {(m.unsupported_factors && m.unsupported_factors.length > 0) && (
                  <span className="mono" title={'这些因子无法求值,未参与训练:' + m.unsupported_factors.join(', ')}
                    style={{ fontSize: 8.5, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 4, padding: '0 4px', marginLeft: 4, flexShrink: 0 }}>⚠ {m.unsupported_factors.length} 未用</span>
                )}
              </div>
              <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>
                {(m.n_features != null ? m.n_features : '?') + '因子'} · 留出 OOS {m.oos_ic != null ? (m.oos_ic >= 0 ? '+' : '') + (+m.oos_ic).toFixed(3) : '—'}{m.asof ? ' · ' + m.asof : ''}
              </div>
            </div>
            <span onClick={() => runValidate(m.id, 'quick')} title="快验:DSR + 夏普快速估算" className="mono" style={{ fontSize: 9.5, color: 'var(--dai)', cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 6px', flexShrink: 0 }}>快验</span>
            <span onClick={() => runValidate(m.id, 'strict')} title="严格验证(CPCV ~分钟级):完成后回灌 DSR/夏普" className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 6px', flexShrink: 0 }}>严格验证</span>
            <span onClick={() => delVariant(m.id, m.name)} title="删除变体" className="mono" style={{ fontSize: 12, color: 'var(--ink-3)', cursor: 'pointer', padding: '2px 6px', flexShrink: 0 }}>✕</span>
          </div>
        ))}
        <div className="serif" style={{ fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 8 }}>留出验证 OOS · 非未来实盘(时间留出,非向前 vintage)。</div>
      </div>
    </div>
  );
}

// ───────── 主组件 ─────────
function XuanguApp() {
  const [cfg, setCfg] = useState(defaultCfg);
  const [sort, setSort] = useState({ k: 'score', d: -1 });
  const [dark, setDark] = useState(false);
  const [toast, setToast] = useState(null);
  const [committed, setCommitted] = useState(null);
  const [showBench, setShowBench] = useState(true);
  const [expanded, setExpanded] = useState({});
  const [picking, setPicking] = useState(false);
  const [showWs, setShowWs] = useState(false);   // v4 模型工坊抽屉

  useEffect(() => { document.body.classList.toggle('dark', dark); }, [dark]);

  // 从工作流 / 图谱带因子进来(P1⑨:旧版只认 4 个 demo 名,真因子被静默丢弃 → 改全目录解析)
  useEffect(() => {
    const h = (!ISOLATED && window.GL) ? GL.take('screen', WW_WS) : null;  // ISOLATED:不接收工作流/图谱带来的因子;WW_WS=按帷幄会话取信箱
    // 帷幄驱动:整套选股配置(factors/pool/blend/topN)直接落地
    if (h && h.cfg && typeof h.cfg === 'object') {
      const c0 = h.cfg;
      setCfg(c => ({ ...c,
        ...(Array.isArray(c0.factors) ? { factors: c0.factors.map(f => ({ id: String(f.id), w: Number(f.w || 1) })) } : {}),
        ...(c0.pool ? { pool: c0.pool } : {}), ...(c0.blend != null ? { blend: Number(c0.blend) } : {}),
        ...(c0.topN ? { topN: Number(c0.topN) } : {}),
        // 掌控审计 2026-06-15:并入帷幄送来的约束类字段,使可见 UI 与 agent headless 跑同口径(否则「报的≠看到的」)
        ...(c0.liqMin != null ? { liqMin: Number(c0.liqMin) } : {}),
        ...(Array.isArray(c0.mlStatus) ? { mlStatus: c0.mlStatus.map(String) } : {}),
        ...(c0.industryNeutral != null ? { industryNeutral: !!c0.industryNeutral } : {}),
        ...(c0.indCap != null ? { indCap: Number(c0.indCap) } : {}),
        ...(c0.exclST != null ? { exclST: !!c0.exclST } : {}),
        ...(c0.exclHalt != null ? { exclHalt: !!c0.exclHalt } : {}),
        ...(c0.exclLimit != null ? { exclLimit: !!c0.exclLimit } : {}),
        ...(c0.exclNew != null ? { exclNew: !!c0.exclNew } : {}),
        ...(c0.model ? { model: c0.model } : {}) }));
      flash('帷幄令到', '已按帷幄参数选股(α=' + (c0.blend != null ? c0.blend : '·') + ' · ' + (c0.pool || '') + ')');
      refresh();   // cfg 变化不自动重算(tick驱动),帷幄送参后主动触发一次
      return;   // cfg 路径与单因子路径互斥
    }
    if (h && (h.factor || h.name || h.id)) {
      const apply = () => {
        const byId = window.XG_FBYID || {};
        const nm = h.name || '';
        const id = (h.id && byId[h.id]) ? h.id
          : Object.keys(byId).find(k => k === nm || (byId[k].short && byId[k].short === nm)) || null;
        if (id) { setCfg(c => ({ ...c, factors: [{ id, w: 1 }] })); flash('已带入因子', ((byId[id] || {}).short || id) + ' · 已据此打分'); }
        else flash('未识别因子', (nm || h.factor || '').slice(0, 40) + ' 不在选股目录(可先在工作流「存入因子库」)');
      };
      // 目录是异步拉的(/screen/factors 含 factorlib):先确保目录就绪再解析,匹配不上诚实提示
      if (API && window.xgLoadCatalog) window.xgLoadCatalog(API).then(apply).catch(apply); else apply();
    }
  }, []);

  const flash = (title, body) => { setToast({ title, body }); setTimeout(() => setToast(null), 3400); };
  const API = (typeof window !== 'undefined' && window.GUANLAN_BACKEND) || '';
  const [result, setResult] = useState(() => window.xgBuildLocal(cfg));
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);                 // 手动「重算」触发
  const [lastRun, setLastRun] = useState(null);        // 最后一次计算完成时间
  const [dirty, setDirty] = useState(false);           // 参数已变、尚未重算
  const refresh = () => setTick(t => t + 1);
  // 手动计算:仅挂载首跑 + 点「重新计算」(tick++)时跑。**故意不含 cfg** → 改参数不自动重算
  // (单次 ~5s,避免每动一下就跑)。点重算时组件已用最新 cfg 重渲染,故抓到的是最新参数。
  useEffect(() => {
    if (!API) { setResult(window.xgBuildLocal(cfg)); setLastRun(new Date()); setDirty(false); return; }
    let alive = true;
    setLoading(true);
    window.xgBuildBackend(cfg, API)
      .then(r => { if (alive) { setResult(r); setLastRun(new Date()); setDirty(false); } })
      .catch(e => { if (alive) flash('⚠ 选股后端失败', String((e && e.message) || e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [tick]);  // eslint-disable-line react-hooks/exhaustive-deps
  // 参数变动 → 标「待重算」(跳过首次挂载)
  const _mounted = useRef(false);
  useEffect(() => { if (_mounted.current) setDirty(true); else _mounted.current = true; }, [cfg]);

  // ── 动态因子目录(选股页2.0):启动拉 /screen/factors(56因子·11族·实测IC)→ 重渲染因子库 ──
  const [, setCatN] = useState(0);
  useEffect(() => {
    if (!API || !window.xgLoadCatalog) return;
    window.xgLoadCatalog(API).then(n => { if (n) setCatN(n); }).catch(() => {});
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // ── v4 模型工坊:可选模型列表(生产 v4 + 训练出的变体)→ 顶栏模型选择器 ──
  const [models, setModels] = useState([{ id: 'prod', name: '生产 v4', oos_ic: null }]);
  const reloadModels = () => {
    if (!API || !window.xgModels) return;
    window.xgModels(API).then(j => {
      if (j && j.ok) setModels([{ id: 'prod', name: '生产 v4', oos_ic: null }].concat(j.variants || []));
    }).catch(() => {});
  };
  useEffect(reloadModels, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // ── 「拉取最新数据」: 后台引擎原生再生(无 qlib)三产物 → 轮询进度 → 退0热加载缓存后自动重算 ──
  const [regen, setRegen] = useState({ busy: false, phase: 'idle', label: '', step: 0, total: 4, elapsed: 0 });
  const _regenPoll = useRef(null);
  const _applyRegen = (s) => setRegen({ busy: !!s.running, phase: s.phase || 'idle', label: s.label || '', step: s.step || 0, total: s.total || 4, elapsed: s.elapsed_sec || 0 });
  const _pollRegen = () => {
    fetch(API + '/screen/regen/status').then(r => r.json()).then(j => {
      const s = (j && j.state) || {};
      _applyRegen(s);
      if (s.running) { _regenPoll.current = setTimeout(_pollRegen, 2000); }
      else {
        _regenPoll.current = null;
        if (s.ok) { flash('数据已更新 · ' + (s.new_date || ''), '已热加载缓存 · 自动重算'); refresh(); }
        else if (s.error) { flash('⚠ 再生失败', String(s.error)); }
      }
    }).catch(() => { _regenPoll.current = setTimeout(_pollRegen, 3000); });
  };
  const regenData = () => {
    if (!API) { flash('需后端', '「拉取最新数据」需连接 9999 后端'); return; }
    if (regen.busy) return;
    setRegen(r => ({ ...r, busy: true, phase: 'starting', label: '启动子进程…', step: 0 }));
    flash('瀾 拉取最新数据…', '引擎原生再生(无 qlib) · v4 训练约 5 分钟 · 完成自动刷新');
    fetch(API + '/screen/regen', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ end: null }) })
      .then(r => r.json()).then(j => { _applyRegen((j && j.state) || { running: true }); _pollRegen(); })
      .catch(e => { setRegen(r => ({ ...r, busy: false, phase: 'error' })); flash('⚠ 触发再生失败', String((e && e.message) || e)); });
  };
  // 挂载即查一次:已有再生在跑(刷新页/多标签)→ 接管进度;卸载停轮询
  useEffect(() => {
    if (!API) return;
    fetch(API + '/screen/regen/status').then(r => r.json()).then(j => {
      const s = (j && j.state) || {};
      if (s.running) { _applyRegen(s); _pollRegen(); }
    }).catch(() => {});
    return () => { if (_regenPoll.current) { clearTimeout(_regenPoll.current); _regenPoll.current = null; } };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const setF = (patch) => setCfg(c => ({ ...c, ...patch }));
  const toggleFactor = (id) => setCfg(c => {
    const has = c.factors.find(f => f.id === id);
    let factors = has ? c.factors.filter(f => f.id !== id) : [...c.factors, { id, w: 0.8 }];
    if (factors.length === 0) factors = [{ id, w: 1 }]; // 至少一个
    return { ...c, factors };
  });
  const setFactorW = (id, w) => setCfg(c => ({ ...c, factors: c.factors.map(f => f.id === id ? { ...f, w: Math.max(0.1, +w.toFixed(1)) } : f) }));
  const pickFactors = (prompt) => {
    setPicking(true); flash('瀾 正在拆解因子…', API ? '真模型 deepseek · 匹配因子库' : '本地规则 · 匹配因子库');
    const fallback = () => {                                   // 诚实兜底:LLM 不可用→本地正则,明示
      const r = llmPick(prompt);
      setCfg(c => ({ ...c, factors: r.factors, reasons: r.reasons, llmSummary: r.summary }));
      setPicking(false); flash('已列出选股因子 · 本地兜底', r.summary);
    };
    if (!API) { fallback(); return; }
    fetch(API + '/screen/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt }) })
      .then(r => r.json()).then(j => {
        if (!j.ok || !j.factors || !j.factors.length) { fallback(); return; }
        setCfg(c => ({ ...c, factors: j.factors, reasons: j.reasons || {}, llmSummary: j.summary || '' }));
        setPicking(false); flash('已列出选股因子 · ' + (j.model || 'LLM'), j.summary || '');
      }).catch(() => fallback());
  };
  const toggleExpand = (code) => setExpanded(e => ({ ...e, [code]: !e[code] }));

  const runPhrase = (q) => {
    if (!q.trim()) return;
    const local = () => {                                     // 诚实兜底:LLM 不可用→本地正则,明示
      const { cfg: nc, hit } = parsePhrase(q, cfg); setCfg(nc);
      flash(q.trim().slice(0, 20), (hit.length ? hit.join(' · ') : '已按描述调整约束') + ' · 本地兜底');
    };
    if (!API) { local(); return; }
    flash('瀾 正在解析…', '真模型 deepseek · ' + q.trim().slice(0, 16));
    fetch(API + '/screen/phrase', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phrase: q, cfg }) })
      .then(r => r.json()).then(j => {
        if (!j.ok || !j.patch) { local(); return; }
        setCfg(c => ({ ...c, ...j.patch }));
        flash(q.trim().slice(0, 20), (j.hit && j.hit.length ? j.hit.join(' · ') : '已按描述调整约束') + ' · ' + (j.model || 'LLM'));
      }).catch(() => local());
  };

  // ── 据此落子: 写 decision 物料 + handoff ──
  const commit = () => {
    const basket = result.chosen.map(x => ({ code: x.s.code, name: x.s.name, ind: x.s.ind, pct: x.pct }));
    const facs = cfg.factors.map(f => (window.XG_FBYID[f.id] || { short: f.id }).short).join(' + ');
    const title = '选股 · ' + facs + ' · ' + result.chosen.length + ' 只';
    let id = 'decision_' + Date.now().toString(36);
    if (window.GL && !ISOLATED) {   // ISOLATED:不写共享档案、不 handoff 落子(据此落子=本地态)
      id = GL.put({
        type: 'decision', title, status: 'draft', date: TODAY,
        n: result.chosen.length, ic: (result.stat.combIC != null ? (+result.stat.combIC).toFixed(3) : '—'),
        basket, factors: cfg.factors.map(f => f.id),
        refs: cfg.factors.map(f => f.id),
        note: '今日因子选股 → 待落子执行',
      });
      GL.handoff('cockpit', { fromScreen: true, decisionId: id, title, basket });
    }
    setCommitted({ id, title, n: result.chosen.length });
  };

  const reset = () => { setCfg(defaultCfg()); setCommitted(null); flash('已重置约束', '回到默认因子与约束'); };

  return (
    <div className="paper-bg" style={{ height: '100vh', display: 'flex', flexDirection: 'column', minWidth: 1340 }}>
      <TopBar cfg={cfg} result={result} onPhrase={runPhrase} onCommit={commit} dark={dark} setDark={setDark} committed={committed}
        models={models} model={cfg.model} actualModel={result.model} onModel={(v) => { setF({ model: v }); refresh(); }}
        onWorkshop={() => setShowWs(true)} />
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '400px 1fr', minHeight: 0 }}>
        <ConstraintRail cfg={cfg} setF={setF} toggleFactor={toggleFactor} setFactorW={setFactorW} onReset={reset} pickFactors={pickFactors} picking={picking} result={result} committed={committed} onClearCommit={() => setCommitted(null)} />
        <RankTable result={result} cfg={cfg} sort={sort} setSort={setSort} showBench={showBench} setShowBench={setShowBench} expanded={expanded} toggleExpand={toggleExpand} onRefresh={refresh} loading={loading} lastRun={lastRun} dirty={dirty} onRegen={regenData} regen={regen} />
      </div>
      {showWs && <ModelWorkshop API={API} models={models} reloadModels={reloadModels} flash={flash}
        onPick={(id) => { setF({ model: id }); refresh(); }} onClose={() => setShowWs(false)} />}
      {toast && (
        <div style={{ position: 'fixed', top: 60, left: '50%', transform: 'translateX(-50%)', zIndex: 50, display: 'flex', alignItems: 'center', gap: 10, background: 'var(--paper)', border: '1px solid var(--zhu-soft)', borderRadius: 11, padding: '10px 16px', boxShadow: '0 6px 24px rgba(28,24,20,0.16)', animation: 'fadeIn .3s ease', maxWidth: 600 }}>
          <span className="seal" style={{ width: 22, height: 22, fontSize: 12, borderRadius: 6 }}>瀾</span>
          <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)' }}>「<b style={{ color: 'var(--yin)' }}>{toast.title}</b>」 · <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>{toast.body}</span></span>
        </div>
      )}
    </div>
  );
}

// ───────── 顶栏 ─────────
function TopBar({ cfg, result, onPhrase, onCommit, dark, setDark, committed, models, model, actualModel, onModel, onWorkshop }) {
  const [q, setQ] = useState('');
  // 变体口径:真正在用变体(actualModel 非 prod、未回落)时,顶栏「体检 IC / v4 provenance」两枚徽章
  //   原读 prod 全局产物(model_health / v4_b3_provenance),对变体是误标 → 换成变体自身口径。
  const isVariant = !!(actualModel && actualModel !== 'prod');
  const vmeta = isVariant ? (models || []).find(m => m.id === actualModel) : null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '0 18px', height: 50, flexShrink: 0, borderBottom: '1px solid var(--line)', background: 'rgba(241,234,217,0.72)' }}>
      {!WW_EMBED && (<React.Fragment>
      <div className="seal" style={{ width: 26, height: 26 }}>觀</div>
      <span className="serif" style={{ fontSize: 14, fontWeight: 600, letterSpacing: '0.04em' }}>觀瀾 · 选股</span>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 6px' }}>V1.0</span>
      </React.Fragment>)}
      <span style={{ color: 'var(--line)' }}>|</span>
      {result.source === 'v4_ranking' ? (
        <>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>评级池 <b style={{ fontFamily: 'var(--sans)', color: 'var(--ink)' }}>v4 · {result.scored.length}</b></span>
          {models && models.length > 0 && (
            <select value={model} onChange={e => onModel(e.target.value)}
              title="选择 v4 模型:生产 / 训练的变体"
              style={{ fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink)', background: 'var(--paper)',
                       border: '1px solid var(--line)', borderRadius: 6, padding: '2px 6px', marginLeft: 2, cursor: 'pointer' }}>
              {models.map(m => (
                <option key={m.id} value={m.id}>
                  {m.name}{m.oos_ic != null ? ` · OOS ${(+m.oos_ic).toFixed(3)}` : ''}
                </option>
              ))}
            </select>
          )}
          {actualModel && model && actualModel !== model && (
            <span className="mono" title="所选变体不可用,已回落生产 v4"
              style={{ fontSize: 9.5, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 5, padding: '2px 7px' }}>⚠ 变体不可用·已用 {actualModel}</span>
          )}
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>排名日 {result.date}{result.mainline_as_of ? ' · 主线 ' + result.mainline_as_of : ''}</span>
          {result.market && (() => {
            const stale = result.market.as_of && result.date && result.market.as_of < result.date;
            return <span className="mono" title={'L4 V1 节奏 · 涨停残差60日分位 ' + (result.market.lu_pct60 ?? '—') + (stale ? ' · 口径较排名日旧' : '')}
              style={{ fontSize: 10, color: 'var(--paper)', background: stale ? 'var(--ink-3)' : 'var(--dai)', borderRadius: 5, padding: '2px 7px' }}>节奏 · {result.market.stage} @{result.market.as_of ?? '—'}{stale ? ' 旧' : ''}</span>;
          })()}
          {!isVariant && result.model_health && (() => {
            const h = result.model_health;
            const vin = h.vintage && h.vintage.ready;       // 真OOS 积累≥10天 → 主口径切 vintage
            const icShow = vin ? h.vintage.ic_mean : h.recent20;
            const tip = (vin ? ('真OOS vintage IC ' + h.vintage.ic_mean + '(' + h.vintage.n_days + '日)· ') : '')
              + '回看:近20日 ' + h.recent20 + ' / 前40日 ' + (h.prior40 ?? '—') + ' · ' + h.note
              + (h.vintage && !vin ? (' · vintage 积累中 ' + h.vintage.n_days + '/10 日') : '');
            return <span className="mono" title={tip}
              style={{ fontSize: 10, color: h.alert ? 'var(--paper)' : 'var(--ink-2)', background: h.alert ? 'var(--yin)' : 'transparent', border: '1px solid ' + (h.alert ? 'var(--yin)' : 'var(--line)'), borderRadius: 5, padding: '2px 7px' }}>
              体检{vin ? '·OOS' : ''} IC {(icShow >= 0 ? '+' : '') + (+icShow).toFixed(3)} {h.trend}{h.alert ? ' ⚠衰减' : ''}</span>;
          })()}
          {isVariant && (() => {
            // 变体「留出 OOS IC」徽章(替代 prod 回看体检 IC;变体无 model_health.parquet)
            const oi = vmeta ? vmeta.oos_ic : null;
            const tip = vmeta
              ? ('变体「' + (vmeta.name || vmeta.id) + '」· 留出验证 OOS rank-IC '
                  + (oi != null ? ((oi >= 0 ? '+' : '') + (+oi).toFixed(3)) : '—')
                  + (vmeta.oos_icir != null ? ' · ICIR ' + (+vmeta.oos_icir).toFixed(2) : '')
                  + ' · 留出 ' + (vmeta.n_holdout ?? '—') + ' 日 / ' + (vmeta.n_features ?? '—') + ' 特征'
                  + ' · 留出 OOS,非未来实盘;prod 体检口径不适用变体')
              : '变体口径(未取到该变体元信息,可能已删除)';
            return <span key="v-oos" className="mono" title={tip}
              style={{ fontSize: 10, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 7px' }}>
              变体·留出OOS IC {oi != null ? ((oi >= 0 ? '+' : '') + (+oi).toFixed(3)) : '—'}</span>;
          })()}
          {isVariant && (() => {
            // 变体 provenance:纯 LGB 自训(无 FinCast 集成、无 prod look-ahead 口径)+ 缺字段库因子告警
            const uns = (vmeta && vmeta.unsupported_factors) || [];
            const tip = '变体排名口径:纯 LGB(自选特征训练)· '
              + ((vmeta && vmeta.n_features != null) ? vmeta.n_features : '—') + ' 特征'
              + (uns.length ? ' · ⚠ ' + uns.length + ' 个库因子字段缺失未参与:' + uns.join('、') : '')
              + ' · 不含 FinCast 集成,无 prod look-ahead 口径';
            return <span key="v-prov" className="mono" title={tip}
              style={{ fontSize: 10, color: 'var(--ink-1)', border: '1px dashed var(--zhu-soft)', borderRadius: 5, padding: '2px 7px' }}>
              变体 · 纯 LGB{uns.length ? ' ⚠' + uns.length : ''}</span>;
          })()}
          {!isVariant && result.v4_provenance && (() => {
            const p = result.v4_provenance;       // #7 v4 排名口径:纯 LGB vs LGB+FinCast(B3 集成),诚实显形
            const la = p.lookahead === true;
            if (p.active) {
              const tip = '排名口径:LGB + FinCast 混合(B3 集成)· w_LGB=' + (+p.w_lgb).toFixed(2) + ' + w_FC=' + (+p.w_fc).toFixed(2)
                + ' · ' + p.n_has_fc + '/' + p.n_total + ' 只匹配 FinCast 预测'
                + (p.fc_icir_recent != null ? ' · FinCast 近期 ICIR ' + (+p.fc_icir_recent).toFixed(3) + '(权重按此自适应)' : '')
                + (la ? ' · ⚠ 该日含模型 look-ahead(≤ckpt训练截止)' : ' · 零样本,无训练窗 look-ahead')
                + (p.eval_date ? ' · 预测日 ' + p.eval_date : '');
              return <span className="mono" title={tip}
                style={{ fontSize: 10, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 5, padding: '2px 7px' }}>
                v4 · LGB+FinCast w<sub style={{ fontSize: 7 }}>FC</sub>={(+p.w_fc).toFixed(2)}{la ? ' ⚠前视' : ''}</span>;
            }
            return <span className="mono" title={'排名口径:纯 LGB(' + (p.reason || '无当日 FinCast 预测') + ')。混入 FinCast 需离线产出当日预测 parquet。'}
              style={{ fontSize: 10, color: 'var(--ink-3)', border: '1px dashed var(--line)', borderRadius: 5, padding: '2px 7px' }}>v4 · 纯 LGB</span>;
          })()}
          {result.panel_ok === false && (
            <span className="mono" title="引擎面板不可用:现价/成交额/L3 量能/L4 位置指标缺失(非数据稀疏)" style={{ fontSize: 10, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 5, padding: '2px 7px' }}>⚠ 引擎离线</span>
          )}
        </>
      ) : (
        <>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>股票池 <b style={{ fontFamily: 'var(--sans)', color: 'var(--ink)' }}>沪深主流 · {window.XG_UNIVERSE.length}</b></span>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>调仓日 {TODAY} · 离线示例</span>
        </>
      )}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
        {WW_LEGACY && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(168,57,45,0.06)', border: '1px solid var(--zhu-soft)', borderRadius: 20, padding: '5px 8px 5px 11px' }}>
          <span className="seal" style={{ width: 18, height: 18, fontSize: 10, borderRadius: 5 }}>瀾</span>
          <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { onPhrase(q); setQ(''); } }} placeholder="一句话调约束 · 如「更集中、行业均衡」…"
            style={{ width: 246, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink)' }} />
          <span onClick={() => { onPhrase(q); setQ(''); }} style={{ background: 'var(--yin)', color: 'var(--paper)', borderRadius: 14, padding: '4px 11px', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer' }}>调 ↵</span>
        </div>
        )}
        <span onClick={onWorkshop} title="v4 模型工坊 · 选因子训练命名变体" className="mono" style={{ fontSize: 11, color: 'var(--ink-1)', cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 7, padding: '6px 10px' }}>⚙ 模型工坊</span>
        <span onClick={() => setDark(d => !d)} title="昼/夜" className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 7, padding: '6px 9px' }}>{dark ? '☾' : '☀'}</span>
        <span onClick={onCommit} className="serif" style={{ display: 'flex', alignItems: 'center', gap: 7, background: 'var(--ink)', color: 'var(--paper)', borderRadius: 8, padding: '8px 15px', fontSize: 12.5, cursor: 'pointer' }}>
          <span style={{ fontSize: 13 }}>落</span> 据此落子 · {result.chosen.length} 只
        </span>
      </div>
    </div>
  );
}

// ───────── 小控件 ─────────
function Toggle({ on, onClick }) {
  return (
    <span onClick={onClick} style={{ width: 32, height: 18, borderRadius: 10, background: on ? 'var(--yin)' : 'var(--line)', position: 'relative', cursor: 'pointer', flexShrink: 0, transition: 'background .15s' }}>
      <span style={{ position: 'absolute', top: 2, left: on ? 16 : 2, width: 14, height: 14, borderRadius: '50%', background: 'var(--paper)', transition: 'left .15s', boxShadow: '0 1px 2px rgba(0,0,0,0.2)' }} />
    </span>
  );
}
function Slider({ value, min, max, step, onChange, fmt, disabled }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, opacity: disabled ? 0.4 : 1, pointerEvents: disabled ? 'none' : 'auto' }}>
      <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(+e.target.value)}
        style={{ flex: 1, accentColor: 'var(--yin)', cursor: 'pointer' }} />
      <span className="mono" style={{ fontSize: 11, color: 'var(--ink)', minWidth: 42, textAlign: 'right', fontWeight: 600 }}>{fmt ? fmt(value) : value}</span>
    </div>
  );
}
function Seg({ value, opts, onChange }) {
  return (
    <div style={{ display: 'flex', border: '1px solid var(--line)', borderRadius: 7, overflow: 'hidden' }}>
      {opts.map(([v, l]) => (
        <span key={v} onClick={() => onChange(v)} style={{ flex: 1, textAlign: 'center', padding: '5px 0', fontFamily: 'var(--sans)', fontSize: 11, cursor: 'pointer',
          background: value === v ? 'var(--ink)' : 'transparent', color: value === v ? 'var(--paper)' : 'var(--ink-2)' }}>{l}</span>
      ))}
    </div>
  );
}
function RailSection({ label, hint, children }) {
  return (
    <div style={{ padding: '14px 14px 13px', borderBottom: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <span style={{ width: 3, height: 12, background: 'var(--yin)', borderRadius: 1, flexShrink: 0 }} />
        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)', letterSpacing: '.02em' }}>{label}</span>
        {hint && <span className="mono" style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.04em' }}>{hint}</span>}
      </div>
      {children}
    </div>
  );
}

// ───────── LLM 选因子 面板 ─────────
function LLMFactorPicker({ cfg, pickFactors, picking }) {
  const [p, setP] = useState('');
  const presets = ['震荡市超跌反弹', '资金面+业绩共振', '消息催化打板', '低波防守'];
  return (
    <div style={{ padding: '13px 14px', borderBottom: '1px solid var(--line)', background: 'rgba(168,57,45,0.05)' }}>
      <div className="mono" style={{ fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)', marginBottom: 9, display: 'flex', alignItems: 'center', gap: 6 }}><span className="seal" style={{ width: 15, height: 15, fontSize: 8, borderRadius: 4 }}>瀾</span>LLM 选因子</div>
      <textarea value={p} onChange={e => setP(e.target.value)} rows={2} placeholder="描述选股思路 → LLM 拆成因子。如:震荡市找超跌反弹,叠加资金面与消息催化,规避高位退潮…"
        style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 10px', fontFamily: 'var(--serif)', fontSize: 11.5, color: 'var(--ink)', background: 'var(--paper)', outline: 'none', resize: 'none', lineHeight: 1.5 }} />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, margin: '7px 0' }}>
        {presets.map(pr => <span key={pr} onClick={() => setP(pr)} className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 12, padding: '2px 8px', cursor: 'pointer' }}>{pr}</span>)}
      </div>
      <div onClick={picking ? undefined : () => pickFactors(p)} className="serif" style={{ textAlign: 'center', fontSize: 12, color: 'var(--paper)', background: picking ? 'var(--ink-3)' : 'var(--yin)', borderRadius: 7, padding: '8px', cursor: picking ? 'default' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
        {picking ? <><span style={{ animation: 'pulse 1s infinite' }}>●●●</span> 拆解中</> : <>瀾 列出选股因子</>}
      </div>
      {cfg.llmSummary && !picking && <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 9, lineHeight: 1.55, textWrap: 'pretty' }}>{cfg.llmSummary}</div>}
    </div>
  );
}

// ───────── 因子库(选股页2.0:~56 因子 · 11 族 · 实测IC · 分族折叠)─────────
function FactorLibrary({ cfg, toggleFactor, setFactorW }) {
  const [openFam, setOpenFam] = useState({});
  const [q, setQ] = useState('');
  const all = window.XG_FACTORS || [];
  const selIds = cfg.factors.map(f => f.id);
  const meta = (id) => window.XG_FBYID[id] || { id, short: id, cat: '—', color: 'var(--ink-2)', glyph: '?', ic: null, desc: '' };
  const icStr = (f) => f.ic == null ? 'IC —' : 'IC ' + (f.ic >= 0 ? '+' : '') + (+f.ic).toFixed(3);
  const dyn = all.length > 8;                       // 动态目录已加载(静态兜底只有4条)
  const ql = q.trim().toLowerCase();
  const hit = (f) => !ql || (f.short + f.cat + (f.desc || '')).toLowerCase().includes(ql);
  // 未选因子按族分组(保持后端族序);搜索时全展开直接列命中
  const fams = (window.XG_FAMILIES && window.XG_FAMILIES.length) ? window.XG_FAMILIES : [...new Set(all.map(f => f.cat))];
  const byFam = {}; all.forEach(f => { if (!selIds.includes(f.id) && hit(f)) (byFam[f.cat] = byFam[f.cat] || []).push(f); });
  return (
    <RailSection label="因子库" hint={dyn ? all.length + ' 因子 · ' + (window.XG_IC_NOTE ? '实测IC' : 'IC待算') : '离线 · 静态示例'}>
      {/* 已选:置顶卡(权重滑块 + LLM理由) */}
      {cfg.factors.map(sf => {
        const f = meta(sf.id);
        return (
          <div key={f.id} style={{ border: '1px solid var(--line)', borderRadius: 9, background: 'var(--paper)', padding: '8px 9px', marginBottom: 7 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span onClick={() => toggleFactor(f.id)} style={{ width: 20, height: 20, flexShrink: 0, background: f.color, border: '1px solid ' + f.color, color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 11, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 4, cursor: 'pointer' }}>{f.glyph}</span>
              <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={() => toggleFactor(f.id)} title={f.desc || ''}>
                <div className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)' }}>{f.short}{cfg.reasons && cfg.reasons[f.id] && <span className="mono" style={{ fontSize: 8, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 3, padding: '0 4px', marginLeft: 5 }}>荐</span>}{f.dir < 0 && <span className="mono" style={{ fontSize: 8, color: 'var(--yin)', marginLeft: 5 }}>惩罚层</span>}</div>
                <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{f.cat} · {icStr(f)}{f.icReal ? '·实测' : ''}</div>
              </div>
              <Toggle on={true} onClick={() => toggleFactor(f.id)} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
              <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', width: 28 }}>权重</span>
              <input type="range" min={0.1} max={2} step={0.1} value={sf.w} onChange={e => setFactorW(f.id, +e.target.value)} style={{ flex: 1, accentColor: f.color, cursor: 'pointer' }} />
              <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink)', width: 26, textAlign: 'right' }}>{sf.w.toFixed(1)}</span>
            </div>
            {cfg.reasons && cfg.reasons[f.id] && (
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <span style={{ color: 'var(--yin)', fontSize: 10, lineHeight: 1.5, flexShrink: 0 }}>瀾</span>
                <span className="serif" style={{ fontSize: 10.5, color: 'var(--ink-2)', lineHeight: 1.5, textWrap: 'pretty' }}>{cfg.reasons[f.id]}</span>
              </div>
            )}
          </div>
        );
      })}
      {/* 检索 + 分族折叠库 */}
      {dyn && <input value={q} onChange={e => setQ(e.target.value)} placeholder="搜因子名/族/说明…" className="mono"
        style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 9px', fontSize: 10, color: 'var(--ink)', background: 'var(--paper)', outline: 'none', margin: '2px 0 8px' }} />}
      {fams.map(fam => {
        const fs = byFam[fam] || [];
        if (!fs.length) return null;
        const open = ql ? true : !!openFam[fam];
        const famColor = (fs[0] || {}).color || 'var(--ink-3)';
        const best = fs.reduce((a, f) => Math.max(a, f.ic == null ? -1 : Math.abs(f.ic)), -1);
        return (
          <div key={fam} style={{ marginBottom: 6 }}>
            <div onClick={() => setOpenFam(o => ({ ...o, [fam]: !o[fam] }))} style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer', padding: '5px 2px', userSelect: 'none' }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: famColor, flexShrink: 0 }} />
              <span className="serif" style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink-1)' }}>{fam}</span>
              <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{fs.length}{best >= 0 ? ' · 最佳|IC| ' + best.toFixed(3) : ''}</span>
              <span className="mono" style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--ink-3)' }}>{open ? '▾' : '▸'}</span>
            </div>
            {open && fs.map(f => (
              <div key={f.id} onClick={() => toggleFactor(f.id)} title={(f.desc || '') + (f.expr ? '\n' + f.expr : '')} className="hover-row"
                style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '4px 6px 4px 17px', cursor: 'pointer', borderRadius: 6 }}>
                <span className="serif" style={{ fontSize: 11.5, color: 'var(--ink-1)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.short}</span>
                <span className="mono" style={{ fontSize: 9, color: f.ic == null ? 'var(--ink-3)' : (f.ic >= 0 ? 'var(--zhu)' : 'var(--dai)'), flexShrink: 0 }}>{icStr(f)}</span>
                <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', flexShrink: 0 }}>+</span>
              </div>
            ))}
          </div>
        );
      })}
      {dyn && window.XG_IC_NOTE && <div className="serif" style={{ fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 4 }}>{window.XG_IC_NOTE}</div>}
    </RailSection>
  );
}

// ───────── 左栏 · 条件|决策 双页签 ─────────
function ConstraintRail({ cfg, setF, toggleFactor, setFactorW, onReset, pickFactors, picking, result, committed, onClearCommit }) {
  const rowLabel = { fontSize: 11.5, color: 'var(--ink-2)' };
  const [tab, setTab] = useState('cond');
  const _dec = result.decision || {};
  const nDeci = ((_dec.final || _dec.holdings || [])).length;
  const tabBtn = (k, l, badge) => (
    <span onClick={() => setTab(k)} className="serif"
      style={{ flex: 1, textAlign: 'center', fontSize: 12.5, fontWeight: 600, padding: '8px 0', cursor: 'pointer', color: tab === k ? 'var(--paper)' : 'var(--ink-2)', background: tab === k ? 'var(--yin)' : 'transparent', borderRadius: 7, position: 'relative' }}>
      {l}{badge > 0 && <span className="mono" style={{ fontSize: 8.5, marginLeft: 5, background: tab === k ? 'rgba(241,234,217,0.25)' : 'var(--zhu)', color: 'var(--paper)', borderRadius: 8, padding: '0 5px' }}>{badge}</span>}
    </span>
  );
  return (
    <aside style={{ borderRight: '1px solid var(--line)', background: 'rgba(241,234,217,0.45)', overflowY: 'auto', minHeight: 0 }}>
      {result.source === 'v4_ranking' && (
        <div style={{ padding: '9px 14px', borderBottom: '1px solid var(--line)', background: 'rgba(74,107,92,0.08)' }}>
          <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-1)', lineHeight: 1.55, textWrap: 'pretty' }}>
            <b style={{ color: 'var(--dai)' }}>v4 模型榜</b> · 模型分固定(LGB+五维);所选因子经<b style={{ color: 'var(--ink)' }}>「因子混合 α」</b>参与重排(α&lt;1 生效);约束实时生效。
          </div>
        </div>
      )}
      <div style={{ display: 'flex', gap: 5, padding: '9px 12px', borderBottom: '1px solid var(--line)', background: 'var(--paper)', position: 'sticky', top: 0, zIndex: 5 }}>
        {tabBtn('cond', '① 条件 · 思路与约束', 0)}
        {tabBtn('deci', '② 决策 · 持仓与概览', nDeci)}
      </div>
      {tab === 'deci' ? (<>
        <DecisionPanel result={result} />
        <OverviewPanel result={result} cfg={cfg} committed={committed} onClearCommit={onClearCommit} />
      </>) : (<>
      {WW_LEGACY && <LLMFactorPicker cfg={cfg} pickFactors={pickFactors} picking={picking} />}
      <FactorLibrary cfg={cfg} toggleFactor={toggleFactor} setFactorW={setFactorW} />

      <RailSection label="选股约束">
        <div style={{ marginBottom: 13 }}>
          <div style={{ ...rowLabel, marginBottom: 6 }}>股票池</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {[['all', '全A'], ['csi300', '沪深300'], ['csi500', '中证500'], ['csi800', '中证800'], ['csi1000', '中证1000']].map(([v, l]) => (
              <span key={v} onClick={() => setF({ pool: v })} className="mono"
                style={{ fontSize: 10, cursor: 'pointer', padding: '4px 9px', borderRadius: 14, border: '1px solid ' + ((cfg.pool || 'all') === v ? 'var(--yin)' : 'var(--line)'), background: (cfg.pool || 'all') === v ? 'rgba(168,57,45,0.07)' : 'transparent', color: (cfg.pool || 'all') === v ? 'var(--yin)' : 'var(--ink-3)' }}>{l}</span>
            ))}
          </div>
          <div className="serif" style={{ fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 5 }}>全A=v4 五维评级榜;指数池=成份内按模型分位(lgb_pct)排。</div>
        </div>
        <div>
          <div style={{ ...rowLabel, marginBottom: 6 }}>选股数量 TopN</div>
          <Slider value={cfg.topN} min={5} max={40} step={1} onChange={v => setF({ topN: v })} fmt={v => v + ' 只'} />
        </div>
        {result.source === 'v4_ranking' && (
          <div style={{ marginTop: 13 }}>
            <div style={{ ...rowLabel, marginBottom: 6, display: 'flex', justifyContent: 'space-between' }}>
              <span>因子混合 α</span>
              <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>1=纯v4 · 0=纯因子</span>
            </div>
            <Slider value={cfg.blend} min={0} max={1} step={0.1} onChange={v => setF({ blend: v })}
              fmt={v => v >= 0.999 ? '纯 v4 模型' : (v <= 0.001 ? '纯因子重排' : `v4 ${Math.round(v * 100)}% · 因子 ${Math.round((1 - v) * 100)}%`)} />
            <div className="serif" style={{ fontSize: 10, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 6, textWrap: 'pretty' }}>
              右侧排序 = α·v4模型分 +(1-α)·所选因子复合分。<b style={{ color: 'var(--ink-2)' }}>调低 α 让因子权重真正改右侧</b>;因子库全部因子(含大盘共振/跟随)均可参与,逐因子实测 RankIC 见因子卡。
            </div>
          </div>
        )}
      </RailSection>

      <RailSection label="主线筛选" hint="保留选中状态">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {[['mainline', '主线'], ['initiation', '启动'], ['revival', '二波'], ['decay', '退潮'], ['cold', '冷门'], ['neutral', '中性']].map(([v, l]) => {
            const on = (cfg.mlStatus || []).includes(v);
            return (
              <span key={v} onClick={() => { const cur = cfg.mlStatus || []; setF({ mlStatus: on ? cur.filter(x => x !== v) : [...cur, v] }); }} className="mono"
                style={{ fontSize: 10, cursor: 'pointer', padding: '4px 9px', borderRadius: 14, border: '1px solid ' + (on ? 'var(--dai)' : 'var(--line)'), background: on ? 'rgba(74,107,92,0.10)' : 'transparent', color: on ? 'var(--dai)' : 'var(--ink-3)' }}>{on ? '✓ ' : ''}{l}</span>
            );
          })}
        </div>
        <div className="serif" style={{ fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 5 }}>全选=不筛;取消某状态→该状态行业的票被剔除(如只留 主线+启动 = 只打主线)。</div>
      </RailSection>

      <RailSection label="行业中性">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: cfg.industryNeutral ? 11 : 0 }}>
          <span style={rowLabel}>启用行业均衡</span>
          <Toggle on={cfg.industryNeutral} onClick={() => setF({ industryNeutral: !cfg.industryNeutral })} />
        </div>
        <div style={{ ...rowLabel, marginBottom: 6, opacity: cfg.industryNeutral ? 1 : 0.4 }}>单行业持仓上限</div>
        <Slider value={cfg.indCap} min={0.1} max={0.5} step={0.05} onChange={v => setF({ indCap: v })} fmt={v => (v * 100).toFixed(0) + '%'} disabled={!cfg.industryNeutral} />
      </RailSection>

      <RailSection label="流动性 & 剔除">
        <div style={{ ...rowLabel, marginBottom: 6 }}>成交额下限</div>
        <Slider value={cfg.liqMin} min={0} max={50} step={1} onChange={v => setF({ liqMin: v })} fmt={v => v + ' 亿'} />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 12 }}>
          {[['exclST', '剔除 ST', '名称含 ST'],
            ['exclHalt', '剔除停牌', '排名日无成交 bar(与数据未入库在数据上不可分,口径=当日无成交)'],
            ['exclLimit', '剔除涨跌停', '当日涨跌幅触板:主板 ±9.5% / 双创 ±19.5% / 北交 ±29.5%(与连板同口径)'],
            ['exclNew', '剔除次新', '上市 < 60 个交易日(~130 自然日窗口内首根 bar 起算)']].map(([k, l, tip]) => (
            <span key={k} title={tip} onClick={() => setF({ [k]: !cfg[k] })} className="mono" style={{ fontSize: 10, cursor: 'pointer', padding: '4px 9px', borderRadius: 14, border: '1px solid ' + (cfg[k] ? 'var(--yin)' : 'var(--line)'), background: cfg[k] ? 'rgba(168,57,45,0.07)' : 'transparent', color: cfg[k] ? 'var(--yin)' : 'var(--ink-3)' }}>{cfg[k] ? '✓ ' : ''}{l}</span>
          ))}
        </div>
      </RailSection>

      <div style={{ padding: 14 }}>
        <div onClick={onReset} className="mono" style={{ textAlign: 'center', fontSize: 10.5, color: 'var(--ink-3)', border: '1px dashed var(--line)', borderRadius: 8, padding: '9px', cursor: 'pointer' }}>↺ 重置为默认约束</div>
      </div>
      </>)}
    </aside>
  );
}

// ───────── 因子分位条 ─────────
function PctBar({ pct, color }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      <div style={{ flex: 1, height: 5, background: 'rgba(28,24,20,0.07)', borderRadius: 3, overflow: 'hidden', minWidth: 40 }}>
        <div style={{ width: pct + '%', height: '100%', background: color, opacity: 0.8 }} />
      </div>
      <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)', width: 22, textAlign: 'right' }}>{pct}</span>
    </div>
  );
}

// ───────── 中栏 · 排名清单 ─────────
function RankTable({ result, cfg, sort, setSort, showBench, setShowBench, expanded, toggleExpand, onRefresh, loading, lastRun, dirty, onRegen, regen }) {
  const _fmtT = (d) => { try { return d.toLocaleTimeString('zh-CN', { hour12: false }); } catch (e) { return ''; } };
  const accent = ((window.XG_FBYID[(cfg.factors[0] || {}).id]) || { color: 'var(--zhu)' }).color;
  const pctW = 132;
  const sortRows = (rows) => {
    const k = sort.k, d = sort.d;
    const val = (x) => k === 'score' ? x.score : k === 'pct' ? x.pct : k === 'chg' ? x.s.chg : k === 'price' ? x.s.price : k === 'amt' ? x.s.amt : k === 'weight' ? (x.weight || 0) : k === 'mktcap' ? x.s.mktcap : x.score;
    return rows.slice().sort((a, b) => (val(a) - val(b)) * d);
  };
  const chosen = sortRows(result.chosen);
  const bench = result.benched.slice(0, 14);
  const th = (k, l, w, align) => (
    <div onClick={() => setSort(s => ({ k, d: s.k === k ? -s.d : -1 }))} className="mono" style={{ width: w, flex: w ? 'none' : 1, textAlign: align || 'left', fontSize: 9.5, color: sort.k === k ? 'var(--ink)' : 'var(--ink-3)', letterSpacing: '.04em', cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}>
      {l}{sort.k === k ? <span style={{ color: 'var(--yin)' }}>{sort.d < 0 ? ' ↓' : ' ↑'}</span> : ''}
    </div>
  );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, background: 'var(--paper)' }}>
      {/* 工具条 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '10px 18px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
        <span className="serif" style={{ fontSize: 13.5, fontWeight: 600 }}>今日候选清单</span>
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
          入选 <b style={{ color: 'var(--yin)' }}>{result.chosen.length}</b> · 通过约束 {result.pool.length} · 全池 {result.scored.length}
        </span>
        {result.unsupported_factors && result.unsupported_factors.length > 0 && (
          <span className="mono" title={'这些因子 id 不在目录或无表达式,已被忽略、未参与混合重排(右侧仍为纯 v4 排序):' + result.unsupported_factors.join(', ')}
            style={{ fontSize: 9.5, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 5, padding: '2px 7px', cursor: 'help' }}>
            ⚠ {result.unsupported_factors.length} 个因子未识别·已忽略
          </span>
        )}
        <div style={{ display: 'flex', gap: 5, marginLeft: 6 }}>
          {result.source === 'v4_ranking' ? (
            <span className="mono" style={{ fontSize: 9, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 4, padding: '2px 7px' }}>v4 模型 · LGB + 自适应 + 五维评级(市值分层)</span>
          ) : (
            cfg.factors.map(f => { const m = window.XG_FBYID[f.id] || { glyph: '?', short: f.id, color: 'var(--ink-2)' }; return <span key={f.id} className="mono" style={{ fontSize: 9, color: 'var(--paper)', background: m.color, borderRadius: 4, padding: '2px 7px' }}>{m.glyph} {m.short} ×{f.w.toFixed(1)}</span>; })
          )}
        </div>
        <span className="mono" style={{ marginLeft: 'auto', fontSize: 9, color: dirty ? 'var(--yin)' : 'var(--ink-3)', whiteSpace: 'nowrap' }}>
          {loading ? '计算中…' : (lastRun ? ((dirty ? '参数已变 · 上次 ' : '上次 ') + _fmtT(lastRun)) : '尚未计算')}
        </span>
        <span onClick={(regen && regen.busy) ? undefined : onRegen} className="mono"
          title="拉取最新交易日行情 → 引擎原生再生 v4/主线/节奏(无 qlib,约 5-8 分钟)→ 完成自动热加载并重算"
          style={{ fontSize: 10, color: (regen && regen.busy) ? 'var(--ink-3)' : 'var(--ink-1)', cursor: (regen && regen.busy) ? 'default' : 'pointer', background: 'transparent', border: '1px solid var(--line)', borderRadius: 6, padding: '4px 10px', display: 'flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap' }}>
          <span style={(regen && regen.busy) ? { animation: 'pulse 1s infinite' } : null}>⟲</span>
          {(regen && regen.busy)
            ? ((regen.label || '再生中') + ' (' + (regen.step || 0) + '/' + (regen.total || 4) + (regen.elapsed ? ' · ' + regen.elapsed + 's' : '') + ')')
            : ' 拉取最新数据'}
        </span>
        <span onClick={loading ? undefined : onRefresh} className="mono" title="按当前因子/约束/α/股票池 重新计算"
          style={{ fontSize: 10, color: loading ? 'var(--ink-3)' : 'var(--paper)', cursor: loading ? 'default' : 'pointer', background: loading ? 'transparent' : 'var(--yin)', border: '1px solid var(--yin)', borderRadius: 6, padding: '4px 11px', display: 'flex', alignItems: 'center', gap: 5, boxShadow: (dirty && !loading) ? '0 0 0 2px rgba(168,57,45,0.28)' : 'none' }}>
          <span style={loading ? { animation: 'pulse 1s infinite' } : null}>↻</span>{loading ? ' 计算中' : (dirty ? ' 重新计算 ●' : ' 重新计算')}
        </span>
        <span onClick={() => setShowBench(b => !b)} className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 6, padding: '4px 9px' }}>{showBench ? '隐藏' : '显示'}约束外候选</span>
      </div>
      {/* 表头 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 18px', borderBottom: '1px solid var(--line-soft)', background: 'rgba(28,24,20,0.02)', flexShrink: 0 }}>
        <div style={{ width: 30 }} className="mono">{th('score', '#', 30)}</div>
        <div style={{ flex: 1, minWidth: 0 }} className="mono"><span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>代码 · 名称</span></div>
        {th('mktcap', '行业', 80)}
        {th('pct', '综合分位', pctW, 'left')}
        {th('chg', '当日', 60, 'right')}
        {th('price', '现价', 66, 'right')}
        {th('amt', '成交额', 78, 'right')}
      </div>
      {/* 行 */}
      <div style={{ overflowY: 'auto', minHeight: 0, flex: 1 }}>
        {chosen.map((x, i) => (
          <Row key={x.s.code} x={x} rank={sort.k === 'score' && sort.d < 0 ? i + 1 : null} accent={accent} chosen pctW={pctW} open={!!expanded[x.s.code]} onToggle={() => toggleExpand(x.s.code)} />
        ))}
        {showBench && bench.length > 0 && (
          <>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.1em', padding: '9px 18px 5px', borderTop: '1px solid var(--line)', marginTop: 4 }}>—— 约束外候选 · 未入选 ——</div>
            {bench.map(x => <Row key={x.s.code} x={x} accent={accent} chosen={false} pctW={pctW} open={!!expanded[x.s.code]} onToggle={() => toggleExpand(x.s.code)} />)}
          </>
        )}
        <div style={{ height: 16 }} />
      </div>
    </div>
  );
}

// 护盾徽章配色(L5)
const SHIELD_STYLE = {
  'v4.1': ['金信号', 'var(--jin)'], 'v4.2': ['仕佳险', 'var(--yin)'], 'v4.3': ['涨停5重', 'var(--ink-2)'],
};
function Row({ x, rank, accent, chosen, pctW, open, onToggle }) {
  const s = x.s;
  const reason = x.excl && x.excl.length ? x.excl : (x.benchReason ? [x.benchReason] : []);
  const hasViews = x.views && x.views.length;
  const canExpand = hasViews;
  const _ML = { mainline: ['主线', 'var(--yin)'], revival: ['二波', 'var(--zhu)'], initiation: ['启动', 'var(--jin)'], decay: ['退潮', 'var(--dai)'], cold: ['冷门', 'var(--ink-3)'] };
  const _VR = { distr: '派发', super_distr: '超派', tail_surge: '尾盘冲', bounce: '反弹' };
  const ml = (s.mainline && s.mainline !== 'neutral') ? (_ML[s.mainline] || [s.mainline, 'var(--ink-3)']) : null;
  const _sub = [];
  if (s.v4_total != null) _sub.push('v4 ' + (s.v4_total > 0 ? '+' : '') + s.v4_total);
  if (s.v4_layer) _sub.push(s.v4_layer);
  if (s.lgb_rank != null) _sub.push('LGB#' + s.lgb_rank);
  if (s.vol_regime && _VR[s.vol_regime]) _sub.push(_VR[s.vol_regime]);
  const subText = _sub.join(' · ');
  return (
    <>
      <div className="hover-row" onClick={canExpand ? onToggle : undefined} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 18px', borderBottom: open ? 'none' : '1px solid var(--line-soft)', opacity: chosen ? 1 : 0.5, cursor: canExpand ? 'pointer' : 'default' }}>
        <div style={{ width: 30, flexShrink: 0 }}>
          {rank != null
            ? <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: rank <= 5 ? 'var(--yin)' : 'var(--ink-3)' }}>{rank}</span>
            : <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>·</span>}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap' }}>{s.name}</span>
            <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{s.code}</span>
            {s.st && <span className="mono" style={{ fontSize: 8, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 3, padding: '0 3px' }}>ST</span>}
            {s.limit === 1 && <span className="mono" title="排名日触涨停(板别阈值;开「剔除涨跌停」会剔)" style={{ fontSize: 8, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 3, padding: '0 3px' }}>涨停</span>}
            {s.limit === -1 && <span className="mono" title="排名日触跌停(板别阈值;开「剔除涨跌停」会剔)" style={{ fontSize: 8, color: 'var(--paper)', background: 'var(--dai)', borderRadius: 3, padding: '0 3px' }}>跌停</span>}
            {s.halt && <span className="mono" title="排名日无成交 bar(停牌或数据未入库)" style={{ fontSize: 8, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 3, padding: '0 3px' }}>无成交</span>}
            {s.newish && <span className="mono" title="上市 < 60 个交易日(窗口口径)" style={{ fontSize: 8, color: 'var(--jin)', border: '1px solid var(--line)', borderRadius: 3, padding: '0 3px' }}>次新</span>}
            {s.rating && (
              <span className="mono" title={'L5 评级 · ' + (s.pos_band ? s.pos_band.tier + ' ' + s.pos_band.lo + '-' + s.pos_band.hi + '%' : '')} style={{ fontSize: 10, color: s.stars >= 4 ? 'var(--jin)' : 'var(--ink-3)', letterSpacing: '-1px', fontWeight: 600 }}>{s.rating}</span>
            )}
            {ml && <span className="mono" title="L2 主线雷达 · 行业月级状态" style={{ fontSize: 8.5, color: ml[1], border: '1px solid ' + ml[1], borderRadius: 3, padding: '0 5px', flexShrink: 0 }}>{s.mainline_golden ? '★ ' : ''}{ml[0]}</span>}
            {hasViews && <span className="mono" title="点击展开 L4 九视角" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginLeft: 'auto', flexShrink: 0 }}>九视角 {open ? '▴' : '▾'}</span>}
          </div>
          {(subText || (s.shields || []).length > 0 || reason.length > 0) && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginTop: 3, flexWrap: 'wrap' }}>
              {subText && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.02em' }}>{subText}</span>}
              {(s.shields || []).map(sh => { const stl = SHIELD_STYLE[sh.id] || [sh.name, 'var(--ink-3)']; return <span key={sh.id} className="mono" title={'护盾 ' + sh.id + ' · ' + sh.text} style={{ fontSize: 8, color: stl[1] }}>{sh.level === 'exception' ? '例外·' : ''}{stl[0]}</span>; })}
              {reason.map(r => <span key={r} className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{r}</span>)}
            </div>
          )}
        </div>
        <div style={{ width: 80, flexShrink: 0 }} className="mono"><span style={{ fontSize: 10, color: 'var(--ink-2)' }}>{s.ind}</span></div>
        <div style={{ width: pctW, flexShrink: 0 }}><PctBar pct={x.pct} color={accent} /></div>
        <div style={{ width: 60, flexShrink: 0, textAlign: 'right' }} className="mono"><span style={{ fontSize: 11.5, color: upc(s.chg / 100) }}>{s.chg >= 0 ? '+' : ''}{s.chg.toFixed(2)}%</span></div>
        <div style={{ width: 66, flexShrink: 0, textAlign: 'right' }} className="mono"><span style={{ fontSize: 11.5, color: 'var(--ink-1)' }}>{s.price.toFixed(2)}</span></div>
        <div style={{ width: 78, flexShrink: 0, textAlign: 'right' }} className="mono"><span style={{ fontSize: 11, color: 'var(--ink-2)' }}>{s.amt.toFixed(1)}亿</span></div>
      </div>
      {open && hasViews && <NineViewDetail x={x} />}
    </>
  );
}

// ───────── L4 九视角 readout (展开行内) ─────────
const CONF_STYLE = { data: ['实', 'var(--dai)'], proxy: ['代', 'var(--jin)'], gap: ['缺', 'var(--ink-3)'] };
function NineViewDetail({ x }) {
  const views = x.views || [];
  return (
    <div style={{ padding: '8px 22px 13px 58px', background: 'rgba(74,107,92,0.04)', borderBottom: '1px solid var(--line-soft)', animation: 'fadeIn .2s ease' }}>
      <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.1em', margin: '6px 0 8px' }}>
        L4 九视角 · 观察读数(非决策树) · <span style={{ color: 'var(--dai)' }}>实</span>=真数据 · <span style={{ color: 'var(--jin)' }}>代</span>=代理 · <span style={{ color: 'var(--ink-3)' }}>缺</span>=需材料
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 18px' }}>
        {views.map(v => {
          const cf = CONF_STYLE[v.conf] || CONF_STYLE.gap;
          return (
            <div key={v.v} style={{ display: 'flex', alignItems: 'baseline', gap: 7, opacity: v.conf === 'gap' ? 0.62 : 1 }}>
              <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', width: 50, flexShrink: 0 }}>{v.v} {v.name}</span>
              <span className="mono" style={{ fontSize: 8, color: 'var(--paper)', background: cf[1], borderRadius: 3, padding: '0 3px', flexShrink: 0 }}>{cf[0]}</span>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)' }}>{v.label}
                <span className="serif" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginLeft: 6 }}>{v.evidence}</span>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ───────── L5 决策 · ≤5 持仓收敛 (五维评级 + 护盾 + 仓位档 + 操作) ─────────
function DecisionPanel({ result }) {
  const [llm, setLlm] = useState(null);          // { ok, market_read, holdings:[{code,bull,bear,synth,op}], model } | { ok:false, reason }
  const [llmLoading, setLlmLoading] = useState(false);
  const [news, setNews] = useState(null);        // { ok, market_read, market:[{time,title}], by_code, sentiment, as_of, source } | { ok:false, reason }
  const [newsLoading, setNewsLoading] = useState(false);
  if (result.source !== 'v4_ranking' || !result.decision) return null;
  const dec = result.decision;
  const final = dec.final || [];
  const API = (typeof window !== 'undefined' && window.GUANLAN_BACKEND) || '';
  const llmByCode = {}; ((llm && llm.holdings) || []).forEach(h => { llmByCode[h.code] = h; });
  const newsSent = (news && news.ok && news.sentiment) || {};
  const newsRaw = (news && news.ok && news.by_code) || {};

  const runLlm = () => {
    if (llmLoading || !final.length || !API) return;
    setLlmLoading(true); setLlm(null);
    const viewsByCode = {}; (result.chosen || []).forEach(x => { viewsByCode[x.s.code] = x.views || []; });
    const payload = { final: final.map(f => ({ ...f, views: viewsByCode[f.code] || [] })), market: result.market || null };
    fetch(API + '/screen/llm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      .then(r => r.json()).then(j => setLlm(j))
      .catch(e => setLlm({ ok: false, reason: String((e && e.message) || e) }))
      .finally(() => setLlmLoading(false));
  };

  const runNews = () => {
    if (newsLoading || !final.length || !API) return;
    setNewsLoading(true); setNews(null);
    const codes = final.map(f => f.code);
    fetch(API + '/screen/news', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ codes }) })
      .then(r => r.json()).then(j => setNews(j))
      .catch(e => setNews({ ok: false, reason: String((e && e.message) || e) }))
      .finally(() => setNewsLoading(false));
  };

  return (
    <div style={{ borderBottom: '1px solid var(--line)', background: 'rgba(74,107,92,0.07)' }}>
      <div style={{ padding: '12px 14px 7px' }}>
        <div className="mono" style={{ fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)', display: 'flex', justifyContent: 'space-between' }}>
          <span>L5 决策 · ≤5 持仓收敛</span><span>护盾后 ★★★★+</span>
        </div>
        <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 6, lineHeight: 1.5 }}>
          可执行 <b style={{ color: 'var(--dai)' }}>{dec.n_actionable}</b> 只 → 行业去重收敛 <b style={{ color: 'var(--ink)' }}>{final.length}</b> 只持仓
        </div>
      </div>
      {final.length > 0 && API && (
        <div style={{ padding: '0 14px 10px' }}>
          <div onClick={runLlm} className="serif" style={{ textAlign: 'center', fontSize: 11.5, color: 'var(--paper)', background: llmLoading ? 'var(--ink-3)' : 'var(--yin)', borderRadius: 7, padding: '7px', cursor: llmLoading ? 'default' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
            {llmLoading ? <><span style={{ animation: 'pulse 1s infinite' }}>●●●</span> 瀾 正在点评 ≤5 持仓…</> : <>瀾 LLM 定性点评 · 九视角补缺</>}
          </div>
          {llm && llm.ok === false && (
            <div className="serif" style={{ fontSize: 10, color: 'var(--yin)', lineHeight: 1.5, marginTop: 7 }}>⚠ 点评失败:{llm.reason}(诚实显示,不伪造)</div>
          )}
          {llm && llm.ok && (
            <>
              {llm.market_read && <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-1)', lineHeight: 1.55, marginTop: 8, textWrap: 'pretty' }}><b style={{ color: 'var(--dai)' }}>节奏定性</b> · {llm.market_read}</div>}
              <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginTop: 5 }}>真模型 {llm.model} · 无实时行情,前瞻判断需盘面/材料确认</div>
            </>
          )}
          <div onClick={runNews} className="serif" style={{ textAlign: 'center', fontSize: 11.5, color: 'var(--paper)', background: newsLoading ? 'var(--ink-3)' : 'var(--dai)', borderRadius: 7, padding: '7px', cursor: newsLoading ? 'default' : 'pointer', marginTop: 7, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
            {newsLoading ? <><span style={{ animation: 'pulse 1s infinite' }}>●●●</span> 拉取实时快讯…</> : <>瀾 真·消息面 · 东方财富快讯</>}
          </div>
          {news && news.ok === false && (
            <div className="serif" style={{ fontSize: 10, color: 'var(--yin)', lineHeight: 1.5, marginTop: 7 }}>⚠ 快讯失败:{news.reason}(诚实显示)</div>
          )}
          {news && news.ok && (
            <>
              {news.market_read && <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-1)', lineHeight: 1.55, marginTop: 8, textWrap: 'pretty' }}><b style={{ color: 'var(--dai)' }}>市场消息面</b> · {news.market_read}</div>}
              <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginTop: 5 }}>{news.source} · as_of {news.as_of} · 命中 {(news.covered || []).length}/{final.length} 只(无相关快讯不编造)</div>
            </>
          )}
        </div>
      )}
      {final.length === 0 ? (
        <div className="serif" style={{ padding: '0 14px 12px', fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>当前清单无 ★★★★+ 标的(评级偏中性 → 观望)。</div>
      ) : (
        <div style={{ padding: '0 14px 11px', display: 'flex', flexDirection: 'column', gap: 7 }}>
          {final.map((f, i) => (
            <div key={f.code} style={{ border: '1px solid var(--line)', borderRadius: 9, background: 'var(--paper)', padding: '8px 9px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span className="mono" style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-3)', width: 12, flexShrink: 0 }}>{i + 1}</span>
                <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap' }}>{f.name}</span>
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{f.code}</span>
                <span className="mono" title={f.label} style={{ fontSize: 10, color: f.stars >= 5 ? 'var(--jin)' : 'var(--dai)', letterSpacing: '-1px', marginLeft: 'auto', flexShrink: 0 }}>{f.stars_str}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                <span className="mono" style={{ fontSize: 9, color: 'var(--ink-2)' }}>{f.ind}</span>
                <span className="mono" title="建议仓位区间" style={{ fontSize: 9, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 3, padding: '1px 6px' }}>{f.band.tier} {f.band.lo}-{f.band.hi}%</span>
                {f.mainline_golden && <span className="mono" style={{ fontSize: 8, color: 'var(--paper)', background: 'var(--jin)', borderRadius: 3, padding: '1px 4px' }}>★金信号</span>}
                {(f.shields || []).filter(sh => sh.id !== 'v4.1').map(sh => (
                  <span key={sh.id} className="mono" title={sh.text} style={{ fontSize: 8, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 3, padding: '0 4px' }}>{sh.name}</span>
                ))}
              </div>
              <div className="serif" style={{ fontSize: 10, color: 'var(--ink-2)', lineHeight: 1.5, marginTop: 6, textWrap: 'pretty' }}>瀾 {f.op}</div>
              {llmByCode[f.code] && (() => {
                const h = llmByCode[f.code];
                return (
                  <div style={{ marginTop: 7, paddingTop: 7, borderTop: '1px dashed var(--line)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <div className="mono" style={{ fontSize: 7.5, color: 'var(--ink-3)', letterSpacing: '.08em' }}>瀾 LLM 九视角点评</div>
                    {h.bull && <div className="serif" style={{ fontSize: 10, lineHeight: 1.5, textWrap: 'pretty' }}><b style={{ color: 'var(--zhu)' }}>多</b> {h.bull}</div>}
                    {h.bear && <div className="serif" style={{ fontSize: 10, lineHeight: 1.5, textWrap: 'pretty' }}><b style={{ color: 'var(--dai)' }}>空</b> {h.bear}</div>}
                    {h.synth && <div className="serif" style={{ fontSize: 10, lineHeight: 1.5, color: 'var(--ink-1)', textWrap: 'pretty' }}><b style={{ color: 'var(--yin)' }}>综</b> {h.synth}</div>}
                    {h.op && <div className="serif" style={{ fontSize: 9.5, lineHeight: 1.45, color: 'var(--ink-2)', textWrap: 'pretty' }}><b style={{ color: 'var(--dai)' }}>作</b> {h.op}</div>}
                  </div>
                );
              })()}
              {news && news.ok && (() => {
                const sv = newsSent[f.code]; const raw = newsRaw[f.code];
                if (!raw || !raw.length) return <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>真·消息面:近期无相关快讯(不编造)</div>;
                const tc = sv && sv.tag === '利好' ? 'var(--zhu)' : sv && sv.tag === '利空' ? 'var(--dai)' : 'var(--ink-3)';
                return (
                  <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>
                    <div className="mono" style={{ fontSize: 7.5, color: 'var(--ink-3)', letterSpacing: '.08em' }}>真·消息面 {sv && <span style={{ color: tc, fontWeight: 600 }}>{sv.tag}</span>}</div>
                    {sv && sv.read && <div className="serif" style={{ fontSize: 9.5, color: 'var(--ink-1)', lineHeight: 1.5, marginTop: 3, textWrap: 'pretty' }}>{sv.read}</div>}
                    {raw.slice(0, 2).map((it, k) => <div key={k} className="mono" style={{ fontSize: 8.5, color: 'var(--ink-2)', marginTop: 2, textWrap: 'pretty' }}>· [{it.time}] {it.title}</div>)}
                  </div>
                );
              })()}
            </div>
          ))}
          {(dec.notes || []).map((n, i) => (
            <div key={i} className="serif" style={{ fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.5, display: 'flex', gap: 5 }}><span style={{ color: 'var(--dai)' }}>·</span>{n}</div>
          ))}
        </div>
      )}
    </div>
  );
}

// ───────── 左栏底 · 总体概览 (选出股票分布,无仓位概念) ─────────
const IND_COLORS = ['var(--yin)', 'var(--dai)', 'var(--jin)', 'var(--zhu)', 'var(--ink-2)', 'var(--dai-soft)'];
function OverviewPanel({ result, cfg, committed, onClearCommit }) {
  const st = result.stat;
  const dist = st.indDist.slice(0, 6);
  const other = st.indDist.slice(6).reduce((a, x) => a + x.frac, 0);
  const otherN = st.indDist.slice(6).reduce((a, x) => a + x.n, 0);
  return (
    <div style={{ borderBottom: '1px solid var(--line)', background: 'rgba(168,57,45,0.025)' }}>
      <div style={{ padding: '12px 14px 9px' }}>
        <div className="mono" style={{ fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)', display: 'flex', justifyContent: 'space-between' }}><span>总体概览 · 选出股票分布</span><span>实时</span></div>
      </div>
      {/* 三大指标 */}
      <div style={{ display: 'flex', padding: '0 14px 12px' }}>
        {[['选出', st.n + ' 只', 'var(--ink)'], ['平均分位', st.avgPct.toFixed(0), 'var(--ink-1)'], ['复合 RankIC', (st.combIC != null ? (+st.combIC).toFixed(3) : '—'), 'var(--zhu)']].map(([l, v, c], i) => (
          <div key={i} style={{ flex: 1 }}>
            <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.05em' }}>{l}</div>
            <div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div>
          </div>
        ))}
      </div>
      {/* 行业分布 (按只数) */}
      <div style={{ padding: '0 14px 13px' }}>
        <div className="mono" style={{ fontSize: 9, letterSpacing: '.12em', color: 'var(--ink-3)', marginBottom: 8, display: 'flex', justifyContent: 'space-between' }}><span>行业分布 · 按只数</span><span>{st.indDist.length} 个行业</span></div>
        <div style={{ display: 'flex', height: 9, borderRadius: 3, overflow: 'hidden', marginBottom: 9, background: 'rgba(28,24,20,0.05)' }}>
          {dist.map((d, i) => <div key={d.ind} style={{ width: d.frac * 100 + '%', background: IND_COLORS[i % IND_COLORS.length] }} title={d.ind} />)}
          {other > 0 && <div style={{ width: other * 100 + '%', background: 'var(--line)' }} />}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 16px' }}>
          {dist.map((d, i) => (
            <div key={d.ind} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 7, height: 7, borderRadius: 2, background: IND_COLORS[i % IND_COLORS.length], flexShrink: 0 }} />
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.ind}</span>
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-1)', fontWeight: 600 }}>{d.n}</span>
            </div>
          ))}
          {otherN > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 7, height: 7, borderRadius: 2, background: 'var(--line)', flexShrink: 0 }} />
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', flex: 1 }}>其他</span>
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>{otherN}</span>
            </div>
          )}
        </div>
      </div>
      {/* 已落子 */}
      {committed && (
        <div style={{ padding: '0 14px 13px' }}>
          <div style={{ border: '1px solid var(--dai-soft)', borderRadius: 10, background: 'rgba(74,107,92,0.06)', padding: '11px 12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
              <span className="seal" style={{ width: 18, height: 18, fontSize: 10, borderRadius: 5, background: 'var(--dai)' }}>瀾</span>
              <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)' }}>{ISOLATED ? '已生成本地选股决策' : '已生成选股决策'}</span>
            </div>
            <div className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.5, marginBottom: 9 }}>
              {ISOLATED
                ? committed.n + ' 只 · 本地决策(选股孤岛期,未写入共享档案库;接通主包后再支持溯源/落子)。'
                : committed.n + ' 只 · 已写入共享档案库,可在研究图谱溯源、前往落子执行。'}
            </div>
            {!ISOLATED && (
              <div style={{ display: 'flex', gap: 7 }}>
                <a href="观澜 · 落子.html" className="serif" style={{ flex: 1, textAlign: 'center', fontSize: 11.5, color: 'var(--paper)', background: 'var(--ink)', borderRadius: 7, padding: '7px', textDecoration: 'none' }}>前往落子 →</a>
                <a href="观澜 · 研究图谱.html" className="serif" style={{ flex: 1, textAlign: 'center', fontSize: 11.5, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '7px', textDecoration: 'none' }}>看图谱</a>
              </div>
            )}
            <div onClick={onClearCommit} className="mono" style={{ textAlign: 'center', fontSize: 9.5, color: 'var(--ink-3)', marginTop: 8, cursor: 'pointer' }}>继续选股 ↺</div>
          </div>
        </div>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<XuanguApp />);
