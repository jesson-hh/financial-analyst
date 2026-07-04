/* 观澜 · AI投研 — 河图渲染层(用户重设计稿的 React 忠实移植)。
   三视图:河图 / 全球坐标 / 环节明细。框架来自 /industry/board(YAML 真源),
   行情读数真值(动量/温度/象限/票池 px·chg),研报侧未抽取则显 “—/暂无观点”(不编数)。
   dc 运行时(support.js/x-dc)不移植 —— 以 React.Component 复刻其生命周期/RAF/几何。 */

const QUAD = {
  hh: { label: '双热', c: 'var(--zhu)', dash: 'solid', therm: 'var(--zhu)', fg: 'var(--zhu)', bg: 'var(--zhu-wash)', bd: 'var(--zhu-soft)' },
  lh: { label: '潜伏 · 研报热行情冷', c: 'var(--jin)', dash: 'solid', therm: 'var(--jin)', fg: 'var(--jin-deep)', bg: 'var(--jin-wash)', bd: 'var(--jin-soft)' },
  hl: { label: '情绪 · 行情热研报冷', c: 'var(--zhu)', dash: 'dashed', therm: 'var(--zhu-soft)', fg: 'var(--zhu)', bg: 'var(--paper-2)', bd: 'var(--zhu-soft)' },
  ll: { label: '双冷', c: 'var(--ink-3)', dash: 'solid', therm: 'var(--ink-4)', fg: 'var(--ink-2)', bg: 'var(--paper-2)', bd: 'var(--line-2)' },
};
const MLOGIC = [
  { greek: 'β', name: '全球需求', gloss: '确定性·兑现型' },
  { greek: 'Δ', name: '涨价周期', gloss: '业绩弹性' },
  { greek: 'Ω', name: '国产替代', gloss: '期权弹性' },
  { greek: 'Θ', name: '技术路线', gloss: '期权·高分歧' },
  { greek: 'Ψ', name: '映射主题', gloss: '主题轮动' },
];
const POS_ORDER = { 领先: 0, 并跑: 1, 追赶: 2, 短板: 3, 国内市场: 4 };
const STA = ['短板', '追赶', '并跑', '领先'];
const MIG_SEQ = [
  { seg: 'C2', from: '追赶', to: '并跑', note: '国产化率↑' },
  { seg: 'A1', from: '短板', to: '追赶', note: '大厂订单兑现' },
  { seg: 'G1', from: '追赶', to: '并跑', note: '端侧SoC突破' },
  { seg: 'C4', from: '追赶', to: '并跑', note: '谷歌认证' },
];
const join = (a, s) => (Array.isArray(a) ? a.filter(Boolean).join(s || ' · ') : (a || ''));
const momColor = (m) => (m == null ? 'var(--ink-3)' : m >= 0 ? 'var(--zhu)' : 'var(--dai)');

function narrView(n) {
  const t = n.temp == null ? 0 : n.temp;
  const st = t >= 80 ? { bg: 'var(--zhu)', fg: 'var(--text-on-zhu)', bd: 'var(--zhu)' }
    : t >= 55 ? { bg: 'transparent', fg: 'var(--zhu)', bd: 'var(--zhu-soft)' }
      : t >= 40 ? { bg: 'transparent', fg: 'var(--jin)', bd: 'var(--jin-soft)' }
        : { bg: 'transparent', fg: 'var(--dai)', bd: 'var(--dai-soft)' };
  const barC = t >= 80 ? 'var(--zhu)' : t >= 55 ? 'var(--zhu-soft)' : t >= 40 ? 'var(--jin)' : 'var(--dai-soft)';
  return { stBg: st.bg, stFg: st.fg, stBd: st.bd, barC };
}

class River extends React.Component {
  constructor(p) {
    super(p);
    this.state = {
      board: null, err: null, ing: null, detail: null,
      fw: 'ai_chain', fwList: [],
      view: 'home', tab: 'river', sel: null, zoom: null, hoverId: null, activeNarr: null, hoverLane: null, mig: null,
      showAdjacent: true, restNetwork: false, edgeFlow: true, zoomMs: 420,
    };
    this._merBase = [0, 1, 2, 3, 4, 5].map((i) => (i * Math.PI) / 6);
    this._migIdx = 0;
    this.GROUPS = []; this.SEGS = []; this.DRIVERS = []; this.EDGES = []; this.NARRS = [];
  }

  // ── 数据 ──
  componentDidMount() {
    glFetchFrameworks().then((l) => this.setState({ fwList: Array.isArray(l) ? l : [] }));
    glFetchBoard(false, this.state.fw).then((b) => this.setState(b && b.ok ? { board: b } : { err: (b && b.reason) || '看板不可用' }));
    glIngestState(this.state.fw).then((s) => this.setState({ ing: s }));
    this._t = 0; this._lastTick = 0;
    this._reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    this._onVis = () => { if (!document.hidden) this._startLoop(); };
    document.addEventListener('visibilitychange', this._onVis);
    this._onResize = () => this._scheduleEdges(140);
    window.addEventListener('resize', this._onResize);
    this._startLoop();
  }
  componentWillUnmount() {
    this._dead = true; if (this._raf) cancelAnimationFrame(this._raf);
    if (this._onVis) document.removeEventListener('visibilitychange', this._onVis);
    if (this._onResize) window.removeEventListener('resize', this._onResize);
    clearTimeout(this._mt2); clearTimeout(this._auto); clearTimeout(this._et); clearTimeout(this._zt);
  }
  componentDidUpdate(pp, ps) {
    this._startLoop();
    const isM = this.state.view === 'home' && this.state.tab === 'matrix';
    const isRiver = this.state.view === 'home' && this.state.tab === 'river';
    const vk = this.state.view + '/' + this.state.tab + '/' + this.state.fw + '/' + (this.state.board ? 1 : 0);
    if (vk !== this._lastVK) {
      this._lastVK = vk;
      if (isM) { clearTimeout(this._auto); this._auto = setTimeout(() => { if (this.state.view === 'home' && this.state.tab === 'matrix') this.migrateDemo(); }, 1400); }
      if (isRiver) this._scheduleEdges(120);
    }
    if (ps && (ps.showAdjacent !== this.state.showAdjacent || ps.restNetwork !== this.state.restNetwork) && isRiver) this._scheduleEdges(120);
  }

  refresh() { glFetchBoard(true, this.state.fw).then((b) => this.setState(b && b.ok ? { board: b, err: null } : { err: (b && b.reason) || '看板不可用' })); }
  async ingest() { const r = await glStartIngest(this.state.fw); alert(r.accepted ? '已受理,后台处理中' : `未受理:${r.reason || ''}`); glIngestState(this.state.fw).then((s) => this.setState({ ing: s })); }
  switchFw(id) {
    if (id === this.state.fw) return;
    this.setState({ fw: id, board: null, err: null, detail: null, view: 'home', sel: null, activeNarr: null, hoverId: null, mig: null });
    this._edgesGeo = [];
    glFetchBoard(false, id).then((b) => this.setState(b && b.ok ? { board: b } : { err: (b && b.reason) || '看板不可用' }));
    glIngestState(id).then((s) => this.setState({ ing: s }));
  }

  // ── 适配:board → 设计稿数据形 ──
  _adapt(b) {
    this.GROUPS = (b.groups || []).map((g) => ({ id: g.id, name: g.name }));
    this.SEGS = (b.segments || []).map((s) => {
      if (s.adjacent) return { id: s.id, g: s.group, name: s.display_name || s.name, stub: true };
      const q = s.quant || {}, r = s.research || {};
      const mf = q.momentum20;
      return {
        id: s.id, g: s.group, name: s.display_name || s.name, stub: false,
        mom: mf == null ? null : Math.round(mf * 100), momReason: q.reason,
        quad: s.quadrant || 'll', n30: r.n30, therm: s.therm,
        eq: s.eq || (s.equity_logic || []).join('·'), stars: s.stars || 0, logic: s.logic, kw: s.keywords || [],
        intl: (s.global || {}).intl, cn: (s.global || {}).cn_position, moat: (s.global || {}).moat, prospect: (s.global || {}).prospect,
        mrow: s.mrow, mcol: s.mcol, dual: !!s.dual, good: !!s.good,
      };
    });
    this.DRIVERS = (b.drivers || []).map((d) => ({
      id: d.id, name: d.display_name || d.name, val: d.reading, sub: d.sub,
      up: d.dir === 'up' ? true : d.dir === 'down' ? false : null,
      updates: d.updates || [],
      hint: join(d.indicators) + ((d.updates || []).length ? '\n—— 近30日研报读数证据 ——\n' + d.updates.map((u) => `${(u.publish_ts || '').slice(5, 10)} ${u.org || ''}: ${u.note}`).join('\n') : ''),
    }));
    this.EDGES = (b.edges || []).map((e) => ({ id: e.id, from: e.from, to: e.to, sign: e.sign, mech: e.mechanism, lag: e.lag, valid: join(e.validation) }));
    this.NARRS = (b.narratives || []).map((n) => {
      const act = {}; (n.activates || []).forEach((a) => { act[a.segment] = a.weight; });
      return { id: n.id, name: n.display_name || n.name, status: n.status, temp: n.temp, plus: n.plus7, minus: n.minus7, act, valid: join(n.validation), risk: join(n.risks) };
    });
  }
  _seg(id) { return this.SEGS.find((s) => s.id === id); }
  _nodeName(id) { if (id[0] === 'D') { const d = this.DRIVERS.find((x) => x.id === id); return d ? '驱动·' + d.name : id; } const s = this._seg(id); return s ? s.name : id; }
  _zms() { const v = Number(this.state.zoomMs); return Number.isFinite(v) && v > 0 ? v : 420; }

  // ── 进入 / 返回 ──
  openSeg(sid, e) {
    const r = e.currentTarget.getBoundingClientRect();
    this._rect = { x: r.left, y: r.top, w: r.width, h: r.height };
    this.setState({ zoom: { sid, big: false }, hoverId: null, detail: null });
    glFetchSegment(sid, this.state.fw).then((d) => this.setState({ detail: d }));
    requestAnimationFrame(() => requestAnimationFrame(() => this.setState((s) => (s.zoom ? { zoom: { sid: s.zoom.sid, big: true } } : null))));
    clearTimeout(this._zt);
    this._zt = setTimeout(() => { window.scrollTo(0, 0); this.setState({ view: 'detail', sel: sid, zoom: null }); }, this._zms() + 90);
  }
  goBack() {
    const sid = this.state.sel;
    if (this._rect) {
      this.setState({ view: 'home', zoom: { sid, big: true } });
      requestAnimationFrame(() => requestAnimationFrame(() => this.setState((s) => (s.zoom ? { zoom: { sid: s.zoom.sid, big: false } } : null))));
      clearTimeout(this._zt);
      this._zt = setTimeout(() => this.setState({ zoom: null }), this._zms() + 60);
    } else this.setState({ view: 'home' });
  }
  jump(sid) { window.scrollTo(0, 0); this.setState({ sel: sid, detail: null }); glFetchSegment(sid, this.state.fw).then((d) => this.setState({ detail: d })); }

  // ── 悬停传导边 ──
  _scheduleEdges(delay) {
    clearTimeout(this._et);
    this._et = setTimeout(() => { try { this._computeEdges(); this.forceUpdate(); } catch (e) { if (!this._edgeErr) { this._edgeErr = 1; console.error('edge compute:', e); } } }, delay == null ? 60 : delay);
  }
  _computeEdges() {
    const wrapEl = document.getElementById('gl-riverwrap');
    if (!wrapEl) return;
    const wrap = wrapEl.getBoundingClientRect();
    const rel = (el) => { const r = el.getBoundingClientRect(); return { x: r.left - wrap.left, y: r.top - wrap.top, w: r.width, h: r.height, cx: r.left - wrap.left + r.width / 2, cy: r.top - wrap.top + r.height / 2 }; };
    const NM = {};
    for (const s of this.SEGS) { if (s.stub) continue; const el = document.getElementById('seg-' + s.id); if (el) NM[s.id] = Object.assign(rel(el), { col: this.GROUPS.findIndex((g) => g.id === s.g) }); }
    for (const dv of this.DRIVERS) { const el = document.getElementById('drv-' + dv.id); if (el) NM[dv.id] = Object.assign(rel(el), { driver: true, col: -1 }); }
    const colBox = [];
    document.querySelectorAll('[data-col]').forEach((el) => { const i = +el.getAttribute('data-col'); colBox[i] = rel(el); });
    if (!colBox.length || !colBox[0]) { this._edgesGeo = []; return; }
    const gutter = [];
    for (let k = 0; k < colBox.length - 1; k++) if (colBox[k] && colBox[k + 1]) gutter[k] = (colBox[k].x + colBox[k].w + colBox[k + 1].x) / 2;
    const last = colBox[colBox.length - 1];
    const leftCh = colBox[0].x - 13, rightCh = last.x + last.w + 13;
    let dbot = 0; for (const dv of this.DRIVERS) { const n = NM[dv.id]; if (n) dbot = Math.max(dbot, n.y + n.h); }
    const busBase = dbot + 9;
    const dIdx = {}; this.DRIVERS.forEach((dv, i) => dIdx[dv.id] = i);
    const sIdx = {}; this.SEGS.forEach((s, i) => sIdx[s.id] = i);
    const entryGutter = (col) => col <= 0 ? leftCh : (gutter[col - 1] != null ? gutter[col - 1] : colBox[col].x - 13);
    const rightGutterOf = (col) => gutter[col] != null ? gutter[col] : rightCh;
    const out = [];
    for (const e of this.EDGES) {
      const neg = e.sign === '-';
      for (const f of e.from) for (const t of e.to) {
        const A = NM[f], B = NM[t];
        if (!A || !B) continue;
        const srcOff = A.driver ? (dIdx[f] - 3) * 3.2 : ((sIdx[f] % 6) - 2.5) * 2.4;
        const dOff = A.driver ? (dIdx[f] - 3) * 2.2 : ((sIdx[f] % 6) - 2.5) * 1.6;
        let d, rx, midY;
        if (f === t) { const x = A.x + A.w, y = A.cy; d = `M${x.toFixed(1)},${(y - 6).toFixed(1)} h11 v12 h-11`; rx = x + 11; midY = y; }
        else if (A.driver) { const busY = busBase + dIdx[f] * 4; rx = entryGutter(B.col) + srcOff; const py = B.cy + dOff; d = `M${A.cx.toFixed(1)},${(A.y + A.h).toFixed(1)} V${busY.toFixed(1)} H${rx.toFixed(1)} V${py.toFixed(1)} H${(B.x - 2).toFixed(1)}`; midY = (busY + py) / 2; }
        else if (A.col < B.col) { rx = entryGutter(B.col) + srcOff; const sy = A.cy + dOff, py = B.cy + dOff; d = `M${(A.x + A.w).toFixed(1)},${sy.toFixed(1)} H${rx.toFixed(1)} V${py.toFixed(1)} H${(B.x - 2).toFixed(1)}`; midY = (sy + py) / 2; }
        else if (A.col > B.col) { rx = rightGutterOf(B.col) + srcOff; const sy = A.cy + dOff, py = B.cy + dOff; d = `M${A.x.toFixed(1)},${sy.toFixed(1)} H${rx.toFixed(1)} V${py.toFixed(1)} H${(B.x + B.w + 2).toFixed(1)}`; midY = (sy + py) / 2; }
        else { rx = rightGutterOf(A.col) + Math.abs(srcOff) + 4; const sy = A.cy, py = B.cy; d = `M${(A.x + A.w).toFixed(1)},${sy.toFixed(1)} H${rx.toFixed(1)} V${py.toFixed(1)} H${(B.x + B.w + 2).toFixed(1)}`; midY = (sy + py) / 2; }
        out.push({ id: e.id, f, t, neg, d, lx: rx, ly: midY });
      }
    }
    this._edgesGeo = out;
  }
  _buildEdgeLayer(hoverId) {
    const H = React.createElement;
    const geo = this._edgesGeo || [];
    const flow = this.state.edgeFlow, showRest = this.state.restNetwork;
    const emph = (ed) => !!hoverId && (ed.f === hoverId || ed.t === hoverId);
    const ordered = geo.slice().sort((a, b) => (emph(a) ? 1 : 0) - (emph(b) ? 1 : 0));
    const kids = []; let li = 0;
    ordered.forEach((ed, i) => {
      const on = emph(ed);
      if (!on && !showRest) return;
      const stroke = ed.neg ? '#3E6152' : (on ? '#B23A2B' : '#6A6049');
      const op = on ? 0.97 : (hoverId ? 0.05 : 0.18);
      const sw = on ? 2.3 : 1;
      const dash = ed.neg ? '3 4' : (on && flow ? '7 6' : '');
      if (on) kids.push(H('path', { key: 'h' + i, d: ed.d, fill: 'none', stroke: '#ECE4D2', strokeWidth: 6.5, strokeOpacity: 0.92 }));
      kids.push(H('path', { key: 'e' + i, d: ed.d, fill: 'none', stroke, strokeWidth: sw, strokeOpacity: op, strokeDasharray: dash || undefined, markerEnd: on ? (ed.neg ? 'url(#gl-arrow-dai)' : 'url(#gl-arrow-zhu)') : undefined, strokeLinecap: 'butt', strokeLinejoin: 'round', style: (on && flow && !ed.neg) ? { animation: 'glFlow 1.2s linear infinite' } : undefined }));
      if (on) { const yoff = (li % 3) * 11 - 11; li++; kids.push(H('text', { key: 'l' + i, x: ed.lx, y: ed.ly + yoff, textAnchor: 'middle', style: { fontFamily: 'var(--font-mono)', fontSize: '9.5px', fill: stroke, paintOrder: 'stroke', stroke: '#ECE4D2', strokeWidth: '4.5px' } }, ed.id)); }
    });
    return H(React.Fragment, {}, kids);
  }

  // ── 全球坐标几何 + RAF ──
  _geo() {
    const laneH = 100, n = 5, H = laneH * n, laneLeft = 300, gx = 138, gy = H / 2, R = 90, hubX = 300;
    const parallels = [];
    for (const k of [-62, -32, 0, 32, 62]) { const rx = Math.sqrt(Math.max(1, R * R - k * k)); parallels.push({ cx: gx, cy: gy + k, rx: +rx.toFixed(1), ry: +(rx * 0.24).toFixed(1) }); }
    const meridians = [];
    for (let i = 0; i < 6; i++) { const ph = (i * Math.PI) / 6; meridians.push({ id: 'gl-mer-' + i, cx: gx, cy: gy, ry: R, rx: +(Math.abs(Math.cos(ph)) * R).toFixed(1), stroke: i === 0 ? 'var(--zhu)' : 'var(--ink-3)' }); }
    const rays = [];
    for (let i = 0; i < n; i++) {
      const yi = laneH * i + laneH / 2, ang = Math.atan2(yi - gy, hubX - gx);
      const sx = gx + R * Math.cos(ang), sy = gy + R * Math.sin(ang), ex = hubX, ey = yi;
      const c1x = sx + (ex - sx) * 0.5, c1y = sy, c2x = ex - (ex - sx) * 0.4, c2y = ey;
      rays.push({ sx: +sx.toFixed(1), sy: +sy.toFixed(1), ex, ey, c1x, c1y, c2x, c2y, d: `M${sx.toFixed(1)},${sy.toFixed(1)} C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${ex},${ey}` });
    }
    return { laneH, W: laneLeft, H, laneLeft, gx, gy, R, parallels, meridians, rays };
  }
  _curD(c, t) {
    const pts = [];
    for (let x = 0; x <= 1200; x += 50) { const y = c.baseY + c.amp * Math.sin(c.k * x - t * c.speed + c.phase) + c.amp * 0.4 * Math.sin(c.k * 1.9 * x + t * c.speed * 0.55 + c.phase * 1.7); pts.push(x + ',' + y.toFixed(2)); }
    return 'M' + pts.join(' L');
  }
  _startLoop() {
    const now = performance.now();
    if (this._lastTick && (now - this._lastTick) < 400) return;
    this._dead = false; if (this._raf) cancelAnimationFrame(this._raf);
    const step = () => { if (this._dead) return; this._lastTick = performance.now(); if (!this._reduced) this._t += 0.011; try { this._tick(); } catch (e) { if (!this._errLogged) { this._errLogged = true; console.error('gl tick:', e); } } this._raf = requestAnimationFrame(step); };
    this._raf = requestAnimationFrame(step);
  }
  _tick() {
    const G = this._G; if (!G) return; const t = this._t;
    for (let i = 0; i < 6; i++) { const el = document.getElementById('gl-mer-' + i); if (!el) continue; const c = Math.cos(this._merBase[i] + t); el.setAttribute('rx', Math.max(0.5, Math.abs(c) * G.R).toFixed(1)); el.setAttribute('stroke-opacity', c >= 0 ? '0.82' : '0.24'); }
    const cg = document.getElementById('gl-china'); if (cg) { const a = t * 0.92; cg.setAttribute('transform', 'translate(' + (G.R * 0.86 * Math.sin(a)).toFixed(1) + ',0)'); cg.setAttribute('opacity', Math.cos(a) > 0 ? '1' : '0'); }
    const hov = this._hoverLane;
    for (let i = 0; i < G.rays.length; i++) {
      const r = G.rays[i], heat = (this._axisHeat && this._axisHeat[i]) || 0.4, path = document.querySelector('[data-ray="' + i + '"]');
      if (path) { const hot = hov === i, base = hot ? 2.7 : (this._axisW ? this._axisW[i] : 1.4), amp = this._axisAmp ? this._axisAmp[i] : 0.5, w = base + amp * Math.sin(t * (0.7 + (this._axisSpeed ? this._axisSpeed[i] : 0.6)) + i * 1.3); path.setAttribute('stroke-width', w.toFixed(2)); path.setAttribute('stroke-opacity', hov == null || hot ? (0.45 + 0.55 * heat).toFixed(2) : '0.22'); }
      const d = document.getElementById('gl-dot-' + i); if (!d) continue;
      const p = ((t * (0.05 + 0.13 * heat)) + i * 0.19) % 1, q = 1 - p;
      const x = q * q * q * r.sx + 3 * q * q * p * r.c1x + 3 * q * p * p * r.c2x + p * p * p * r.ex;
      const y = q * q * q * r.sy + 3 * q * q * p * r.c1y + 3 * q * p * p * r.c2y + p * p * p * r.ey;
      d.setAttribute('cx', x.toFixed(1)); d.setAttribute('cy', y.toFixed(1)); d.setAttribute('opacity', (0.2 + 0.8 * Math.sin(Math.PI * p)).toFixed(2));
    }
    if (this._curLines) for (let l = 0; l < this._curLines.length; l++) { const el = document.getElementById('gl-current-' + l); if (el) el.setAttribute('d', this._curD(this._curLines[l], t)); }
  }
  _migMs() { return 3400; }
  migrateDemo() {
    if (this.state.mig) return;
    const m = MIG_SEQ[this._migIdx % MIG_SEQ.length]; this._migIdx += 1;
    this.setState({ mig: { ...m } });
    clearTimeout(this._mt2); this._mt2 = setTimeout(() => this.setState({ mig: null }), this._migMs());
  }

  render() {
    const st = this.state;
    if (st.err) return <div style={{ padding: 40, fontFamily: 'var(--font-serif)', color: 'var(--ink-1)' }}>看板不可用:{st.err}</div>;
    if (!st.board) return <div style={{ padding: 40, color: 'var(--ink-3)' }}>加载中…</div>;
    this._adapt(st.board);
    const fresh = st.board.freshness || {};
    const corpusOk = !!(fresh.corpus && fresh.corpus.ok);
    const isHome = st.view === 'home', isDetail = st.view === 'detail';

    if (!this._curLines) {
      this._curLines = [];
      for (let l = 0; l < 7; l++) this._curLines.push({ id: 'gl-current-' + l, baseY: 6 + l * 13.5, amp: 2.2 + (l % 3) * 0.7, speed: 0.5 + (l % 4) * 0.22, phase: l * 1.3, k: (2 * Math.PI * 1.5 / 1200) * (1 + (l % 2) * 0.4), stroke: l === 2 ? 'var(--zhu)' : 'var(--dai)', width: l % 2 ? 1.4 : 1.0, opacity: l === 2 ? 0.17 : (0.11 + (l % 3) * 0.035) });
    }
    const currents = this._curLines.map((c) => ({ id: c.id, d: this._curD(c, 0), stroke: c.stroke, width: c.width, opacity: c.opacity }));
    this._hoverId = st.hoverId;
    const active = st.activeNarr ? this.NARRS.find((n) => n.id === st.activeNarr) : null;

    return (
      <div style={{ minHeight: '100vh', minWidth: 1600, background: 'var(--paper-page)', fontFamily: 'var(--font-sans)', color: 'var(--ink-1)' }}>
        {this._renderTop(fresh, corpusOk)}
        {isHome && st.tab === 'river' && this._renderRiver(currents, active)}
        {isHome && st.tab === 'matrix' && this._renderMatrix()}
        {isDetail && this._renderDetail()}
        {this._renderZoom()}
        {this._renderFoot(corpusOk)}
      </div>
    );
  }

  _chip(children, key) {
    return <span key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 9px', fontSize: 11, border: '1px solid var(--line-1)', color: 'var(--ink-2)', background: 'var(--paper-2)', borderRadius: 2, whiteSpace: 'nowrap' }}>{children}</span>;
  }
  _renderTop(fresh, corpusOk) {
    const st = this.state;
    const tab = (label, key) => (
      <span key={key} onClick={() => this.setState({ tab: key })} style={{ padding: '6px 14px', fontSize: 12, letterSpacing: 2, cursor: 'pointer', background: st.tab === key ? 'var(--ink-0)' : 'transparent', color: st.tab === key ? 'var(--text-on-ink)' : 'var(--ink-3)' }}>{label}</span>
    );
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 20px', height: 54, borderBottom: '2px solid var(--ink-0)', background: 'var(--paper-0)', position: 'sticky', top: 0, zIndex: 40, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ display: 'inline-flex', width: 26, height: 26, alignItems: 'center', justifyContent: 'center', background: 'var(--zhu)', color: 'var(--text-on-zhu)', fontFamily: 'var(--font-serif)', fontSize: 14, borderRadius: 3 }}>觀</span>
          <span style={{ fontFamily: 'var(--font-serif)', fontSize: 17, fontWeight: 600, letterSpacing: 3, color: 'var(--ink-0)' }}>觀瀾 · AI投研</span>
        </div>
        <span style={{ fontFamily: 'var(--font-serif)', fontSize: 12, letterSpacing: 2, color: 'var(--ink-2)', borderLeft: '1px solid var(--line-2)', paddingLeft: 14 }}>產業鏈河圖</span>
        <span style={{ display: 'flex', border: '1px solid var(--line-2)' }}>
          {(st.fwList.length ? st.fwList : [{ id: st.fw, name: (st.board.meta || {}).name || st.fw }]).map((f) => (
            <span key={f.id} onClick={() => this.switchFw(f.id)} style={{ padding: '5px 12px', fontSize: 11.5, letterSpacing: 1, cursor: 'pointer', whiteSpace: 'nowrap', background: st.fw === f.id ? 'var(--zhu)' : 'transparent', color: st.fw === f.id ? 'var(--text-on-zhu)' : 'var(--ink-3)', fontFamily: 'var(--font-serif)' }}>{f.name}</span>
          ))}
        </span>
        {this._chip(<>{(() => { const m = st.board.meta || {}; return <><b style={{ color: 'var(--ink-1)', fontWeight: 600 }}>{m.name || st.fw}</b>&nbsp;· {m.n_segments != null ? m.n_segments : '—'}环节 · {m.n_drivers != null ? m.n_drivers : '—'}驱动 · {m.n_edges != null ? m.n_edges : '—'}边</>; })()}</>, 'k1')}
        {this._chip(<>语料 {corpusOk ? <b style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink-1)' }}>{(fresh.corpus.latest_publish_ts || '').slice(5, 10) || '有'}</b> : <b style={{ fontFamily: 'var(--font-mono)', color: 'var(--jin)' }} title={fresh.corpus && fresh.corpus.reason}>—</b>}</>, 'k2')}
        {this._chip(<>行情 <b style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink-1)' }}>{fresh.quote_date || '—'}</b></>, 'k3')}
        {this._chip(<><span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--dai)', display: 'inline-block' }} />已抽取 <b style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink-1)' }}>{fresh.extracted_total != null ? fresh.extracted_total : 0}</b> 篇</>, 'k4')}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <span onClick={() => this.setState((s) => ({ restNetwork: !s.restNetwork }))} data-hv="chip" title="显示全部传导边 / 仅悬停" style={{ padding: '4px 9px', fontSize: 11, border: '1px solid var(--line-2)', cursor: 'pointer', color: st.restNetwork ? 'var(--zhu)' : 'var(--ink-3)', background: 'var(--paper-2)' }}>{st.restNetwork ? '● 全网' : '○ 悬停'}</span>
          <button onClick={() => this.ingest()} data-hv="zhu" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '6px 14px', background: 'var(--ink-0)', color: 'var(--text-on-ink)', fontFamily: 'var(--font-serif)', fontSize: 12.5, letterSpacing: 2, cursor: 'pointer', border: 0 }}>處理新研報{st.ing && st.ing.running ? ' · 处理中…' : ''}</button>
          <span onClick={() => this.refresh()} data-hv="chip" style={{ padding: '4px 9px', fontSize: 11, border: '1px solid var(--line-2)', cursor: 'pointer', color: 'var(--ink-2)', background: 'var(--paper-2)' }}>↻ 刷新</span>
          <span style={{ display: 'flex', border: '1px solid var(--line-2)' }}>{tab('河图', 'river')}{tab('全球坐标', 'matrix')}</span>
        </span>
      </div>
    );
  }

  _renderRiver(currents, active) {
    const st = this.state, showAdj = st.showAdjacent;
    const groups = this.GROUPS.map((g, gi) => {
      const segs = this.SEGS.filter((s) => s.g === g.id && (showAdj || !s.stub));
      return { id: g.id, name: g.name, colIdx: gi, delay: (0.05 + gi * 0.05).toFixed(2) + 's', segs };
    });
    return (
      <div style={{ position: 'relative' }} id="gl-riverwrap">
        <svg viewBox="0 0 1200 100" preserveAspectRatio="none" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', zIndex: -1, pointerEvents: 'none' }}>
          {currents.map((c) => <path key={c.id} id={c.id} d={c.d} fill="none" stroke={c.stroke} strokeWidth={c.width} strokeOpacity={c.opacity} vectorEffect="non-scaling-stroke" strokeLinecap="round" />)}
        </svg>
        <svg id="gl-edges" width="100%" height="100%" style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'visible', zIndex: 0 }}>
          <defs>
            <marker id="gl-arrow-ink" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="#6A6049" /></marker>
            <marker id="gl-arrow-dai" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="#3E6152" /></marker>
            <marker id="gl-arrow-zhu" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7.5" markerHeight="7.5" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="#B23A2B" /></marker>
          </defs>
          {this._buildEdgeLayer(st.hoverId)}
        </svg>

        <div style={{ display: 'flex', gap: 12, alignItems: 'stretch', padding: '16px 30px 10px', position: 'relative', zIndex: 1 }}>
          <div style={{ writingMode: 'vertical-rl', fontFamily: 'var(--font-serif)', fontSize: 11, letterSpacing: 4, color: 'var(--ink-3)', borderRight: '1px solid var(--line-1)', paddingRight: 7, marginRight: 2 }}>驅動</div>
          {this.DRIVERS.map((dr, i) => (
            <div key={dr.id} id={'drv-' + dr.id} data-hv="drv" onMouseEnter={() => this.setState({ hoverId: dr.id })} onMouseLeave={() => this.setState({ hoverId: null })} title={dr.hint} style={{ flex: 1, border: '1px solid var(--line-2)', background: 'var(--paper-1)', padding: '8px 11px 7px', position: 'relative', cursor: 'default', animation: 'glRise .5s both', animationDelay: (0.02 + i * 0.04).toFixed(2) + 's' }}>
              <div style={{ fontSize: 11.5, color: 'var(--ink-2)', letterSpacing: .5, display: 'flex', gap: 6, alignItems: 'baseline' }}>{dr.name} <i style={{ fontFamily: 'var(--font-mono)', fontStyle: 'normal', fontSize: 9.5, color: 'var(--ink-3)' }}>{dr.id}</i>{dr.updates.length > 0 && <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--zhu)', border: '1px solid var(--zhu-soft)', padding: '0 4px' }} title="近30日研报读数证据条数(悬停卡片看明细)">研{dr.updates.length}</span>}</div>
              <div style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 12.5, display: 'flex', gap: 6, alignItems: 'baseline' }}><span style={{ color: dr.up === true ? 'var(--zhu)' : dr.up === false ? 'var(--dai)' : 'var(--ink-1)' }}>{dr.val || '—'}</span><span style={{ fontSize: 10, color: 'var(--ink-3)' }}>{dr.sub}</span></div>
            </div>
          ))}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 40, padding: '34px 30px 18px', position: 'relative', zIndex: 1 }}>
          {groups.map((grp) => (
            <div key={grp.id} data-col={grp.colIdx} style={{ display: 'flex', flexDirection: 'column', gap: 12, animation: 'glRise .55s both', animationDelay: grp.delay }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingBottom: 2 }}>
                <span style={{ fontFamily: 'var(--font-serif)', fontSize: 13.5, fontWeight: 600, letterSpacing: 2, color: 'var(--ink-0)' }}>{grp.name}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: 1 }}>{grp.id}</span>
                <span style={{ flex: 1, borderTop: '2px solid var(--ink-0)', opacity: .85 }} />
              </div>
              {grp.segs.map((s) => s.stub ? (
                <div key={s.id} title="相邻链 · 只挂接口不建信号" style={{ border: '1px dashed var(--line-2)', background: 'var(--paper-1)', padding: '8px 9px 7px 10px', opacity: .55 }}>
                  <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{s.name} · 相邻链 ↗</span>
                </div>
              ) : this._renderNode(s, active))}
            </div>
          ))}
        </div>

        {this._renderLegend()}
        {this._renderNarratives(active)}
      </div>
    );
  }

  _renderNode(s, active) {
    const st = this.state, q = QUAD[s.quad] || QUAD.ll;
    let dim = 1, hl = 'none';
    if (active) { const w = active.act[s.id]; if (w == null) dim = 0.28; else if (w >= 0.9) hl = '1.5px solid var(--zhu)'; }
    else if (st.hoverId && st.hoverId !== s.id) { const rel = this.EDGES.some((e) => (e.from.includes(st.hoverId) || e.to.includes(st.hoverId)) && (e.from.includes(s.id) || e.to.includes(s.id))); if (!rel) dim = 0.5; }
    const momTxt = s.mom == null ? '—' : (s.mom >= 0 ? '+' : '') + s.mom + '%';
    return (
      <div key={s.id} id={'seg-' + s.id} data-hv="node" onClick={(e) => this.openSeg(s.id, e)} onMouseEnter={() => this.setState({ hoverId: s.id })} onMouseLeave={() => this.setState({ hoverId: null })} title={s.logic}
        style={{ border: '1px solid var(--line-2)', borderLeft: `3px ${q.dash} ${q.c}`, background: 'var(--paper-1)', padding: '8px 9px 7px 10px', cursor: 'pointer', position: 'relative', transition: 'box-shadow .15s, transform .15s, opacity .25s', opacity: dim, outline: hl, outlineOffset: 1 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink-0)', letterSpacing: .3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</span>
          <span style={{ color: 'var(--jin)', fontSize: 10.5, flex: 'none' }}>{'★'.repeat(s.stars)}</span>
          <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 10.5, flex: 'none', color: momColor(s.mom) }} title={s.momReason || ''}>{momTxt}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 5 }}>
          <span style={{ flex: 1, height: 3, background: 'var(--line-1)', position: 'relative', display: 'block' }}><i style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: (s.therm == null ? 0 : s.therm) + '%', background: q.therm }} /></span>
          <span style={{ fontSize: 9.5, color: 'var(--ink-3)', fontFamily: 'var(--font-mono)', flex: 'none' }}>{s.n30 != null && s.n30 > 0 ? '研+' + s.n30 : '研 —'}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--jin)', flex: 'none' }}>{s.eq}</span>
        </div>
      </div>
    );
  }

  _renderLegend() {
    const sw = (bl, dash) => <span style={{ width: 10, height: 10, border: '1px solid var(--line-2)', borderLeft: `3px ${dash || 'solid'} ${bl}`, background: 'var(--paper-1)', display: 'inline-block' }} />;
    const k = (c) => <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>{c}</span>;
    return (
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', padding: '0 22px 8px', fontSize: 10.5, color: 'var(--ink-3)', flexWrap: 'wrap' }}>
        {k(<>{sw('var(--zhu)')}双热</>)}
        {k(<>{sw('var(--jin)')}潜伏(研报热·行情冷)</>)}
        {k(<>{sw('var(--zhu)', 'dashed')}情绪(行情热·研报冷)</>)}
        {k(<>{sw('var(--ink-3)')}双冷</>)}
        <span style={{ color: 'var(--jin)' }}>★ 好节点(国内领先/并跑 × β/Δ)</span>
        <span>悬停节点或驱动 → 显示传导边(墨线=正向 · <span style={{ color: 'var(--dai)' }}>黛虚线=替代/负向</span>)· 点击节点进入环节明细</span>
      </div>
    );
  }

  _renderNarratives(active) {
    const st = this.state;
    return (
      <>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 22px 6px' }}>
          <span style={{ fontFamily: 'var(--font-serif)', fontSize: 14, fontWeight: 600, letterSpacing: 3, color: 'var(--ink-0)' }}>叙事主线</span>
          <span style={{ fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 1 }}>温度 = 激活环节池加权动量分位 · 点击高亮激活环节</span>
          <span style={{ flex: 1, borderTop: '1px solid var(--line-1)' }} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(8,1fr)', gap: 10, padding: '6px 22px 8px' }}>
          {this.NARRS.map((na, i) => {
            const v = narrView(na), t = na.temp == null ? 0 : na.temp;
            return (
              <div key={na.id} data-hv="soft" onClick={() => this.setState((s) => ({ activeNarr: s.activeNarr === na.id ? null : na.id }))} style={{ border: `1px solid ${st.activeNarr === na.id ? 'var(--zhu)' : 'var(--line-2)'}`, background: 'var(--paper-1)', padding: '9px 11px 8px', cursor: 'pointer', animation: 'glRise .6s both', animationDelay: (0.05 + i * 0.03).toFixed(2) + 's', transition: 'background .15s' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                  <span style={{ fontFamily: 'var(--font-serif)', fontSize: 12.5, fontWeight: 600, letterSpacing: 1, whiteSpace: 'nowrap', color: 'var(--ink-0)' }}>{na.name}</span>
                  <span style={{ marginLeft: 'auto', fontSize: 9, padding: '1px 5px', whiteSpace: 'nowrap', border: `1px solid ${v.stBd}`, color: v.stFg, background: v.stBg }}>{na.status}</span>
                </div>
                <div style={{ marginTop: 8, height: 4, background: 'var(--line-1)', position: 'relative' }}><i style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: t + '%', background: v.barC }} /></div>
                <div style={{ marginTop: 6, fontSize: 9.5, color: 'var(--ink-3)', display: 'flex', gap: 8, fontFamily: 'var(--font-mono)' }}><span>{na.temp == null ? '—' : t + '°'}</span><span style={{ color: 'var(--zhu)' }}>研+{na.plus}</span><span style={{ color: 'var(--dai)' }}>-{na.minus}</span></div>
              </div>
            );
          })}
        </div>
        {active && (
          <div style={{ margin: '2px 22px 10px', border: '1px solid var(--jin-soft)', background: 'var(--jin-wash)', padding: '8px 14px', display: 'flex', gap: 14, alignItems: 'baseline', fontSize: 11, color: 'var(--ink-2)' }}>
            <span style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, letterSpacing: 1, color: 'var(--ink-0)' }}>已高亮 · {active.name} · {active.status}</span>
            <span>验证信号:<span style={{ fontFamily: 'var(--font-mono)' }}>{active.valid}</span></span>
            <span>风险:{active.risk}</span>
            <span onClick={() => this.setState({ activeNarr: null })} style={{ marginLeft: 'auto', cursor: 'pointer', color: 'var(--zhu)', letterSpacing: 1 }}>✕ 取消高亮</span>
          </div>
        )}
      </>
    );
  }

  // ── 全球坐标 ──
  _renderMatrix() {
    const st = this.state, G = this._geo(); this._G = G;
    const AX = ['β', 'Δ', 'Ω', 'Θ', 'Ψ'];
    const heat = AX.map((gk) => { const segs = this.SEGS.filter((s) => !s.stub && s.mcol === gk); const th = segs.map((s) => s.therm).filter((x) => x != null); return th.length ? th.reduce((a, b) => a + b, 0) / (th.length * 100) : 0.4; });
    this._axisHeat = heat; this._axisW = heat.map((h) => 1.1 + h * 2.4); this._axisAmp = heat.map((h) => 0.35 + h * 0.85); this._axisSpeed = heat.map((h) => 0.3 + h * 0.95); this._hoverLane = st.hoverLane;
    const raysV = G.rays.map((r, i) => ({ i, d: r.d, sx: r.sx, sy: r.sy, stroke: st.hoverLane === i ? 'var(--zhu)' : 'var(--ink-3)', width: +this._axisW[i].toFixed(2), opacity: (0.5 + 0.5 * heat[i]).toFixed(2) }));
    const lanes = MLOGIC.map((lg, i) => {
      const items = this.SEGS.filter((s) => !s.stub && s.mcol === lg.greek).sort((a, b) => (POS_ORDER[a.mrow] - POS_ORDER[b.mrow]) || ((b.mom || 0) - (a.mom || 0))).map((s) => {
        const mig = st.mig && st.mig.seg === s.id ? st.mig : null;
        const effPos = mig ? mig.to : s.mrow, idx = STA.indexOf(effPos), market = idx < 0;
        const pips = market ? [] : [0, 1, 2, 3].map((k) => ({ bg: k === idx ? 'var(--zhu)' : k < idx ? 'var(--ink-3)' : 'var(--line-2)', tf: k === idx ? 'scale(1.55)' : 'scale(1)' }));
        return { s, market, pips, star: s.good ? '★' : '', dualTxt: s.dual ? 'Δ+Ω' : '', bd: s.dual ? 'var(--zhu)' : s.good ? 'var(--jin)' : 'var(--line-2)', mig, effPos };
      });
      return { i, greek: lg.greek, name: lg.name, gloss: lg.gloss, count: items.length, items, heatTxt: Math.round(heat[i] * 100) + '°', discBd: heat[i] > 0.78 ? 'var(--zhu)' : heat[i] > 0.55 ? 'var(--jin)' : 'var(--line-3)' };
    });
    return (
      <div style={{ padding: '14px 22px 24px', animation: 'glRise .4s both' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingBottom: 10 }}>
          <span style={{ fontFamily: 'var(--font-serif)', fontSize: 14, fontWeight: 600, letterSpacing: 3, color: 'var(--ink-0)' }}>全球坐标</span>
          <span style={{ fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 1 }}>转动地球 = 中国在全球 AI 产业链的站位 · 五条主逻辑轴(轴粗随热度呼吸)· 环节沿轴悬挂 · 悬停高亮 · 点击进入明细</span>
          <span style={{ flex: 1, borderTop: '1px solid var(--line-1)' }} />
          <span onClick={() => this.migrateDemo()} data-hv="zhu" style={{ flex: 'none', display: 'inline-flex', alignItems: 'center', gap: 6, border: '1px solid var(--zhu)', background: 'var(--zhu-wash)', color: 'var(--zhu)', fontFamily: 'var(--font-serif)', fontSize: 11.5, letterSpacing: 2, padding: '5px 13px', cursor: 'pointer' }}>▶ 演示坐标修正</span>
        </div>
        <div style={{ position: 'relative', minHeight: G.H }}>
          <svg viewBox={`0 0 ${G.W} ${G.H}`} width={G.W} height={G.H} style={{ position: 'absolute', left: 0, top: 0, overflow: 'visible', pointerEvents: 'none' }}>
            {raysV.map((r) => <React.Fragment key={r.i}><path data-ray={r.i} d={r.d} fill="none" stroke={r.stroke} strokeWidth={r.width} strokeOpacity={r.opacity} strokeLinecap="round" /><circle id={'gl-dot-' + r.i} cx={r.sx} cy={r.sy} r="3" fill="var(--zhu)" opacity="0.85" /></React.Fragment>)}
            <circle cx={G.gx} cy={G.gy} r={G.R} fill="var(--paper-2)" stroke="var(--ink-1)" strokeWidth="1.4" />
            {G.parallels.map((p, i) => <ellipse key={i} cx={p.cx} cy={p.cy} rx={p.rx} ry={p.ry} fill="none" stroke="var(--ink-3)" strokeWidth="1" strokeOpacity="0.5" />)}
            {G.meridians.map((mm) => <ellipse key={mm.id} id={mm.id} cx={mm.cx} cy={mm.cy} rx={mm.rx} ry={mm.ry} fill="none" stroke={mm.stroke} strokeWidth="1" strokeOpacity="0.7" />)}
            <circle cx={G.gx} cy={G.gy} r={G.R} fill="none" stroke="var(--ink-1)" strokeWidth="1.4" />
            <g id="gl-china" opacity="1"><circle cx={G.gx} cy={G.gy} r="4.5" fill="var(--zhu)" /><text x={G.gx + 7} y={G.gy - 20} style={{ fontFamily: 'var(--font-serif)', fontSize: '10px', fill: 'var(--zhu)' }}>中</text></g>
          </svg>
          <div style={{ position: 'absolute', left: 0, width: G.W, top: 'calc(100% - 34px)', textAlign: 'center', fontSize: 10, color: 'var(--ink-3)', letterSpacing: 1, pointerEvents: 'none' }}>转动地球 · 中国站位(示意)</div>
          <div style={{ position: 'relative', marginLeft: G.laneLeft }}>
            {lanes.map((lane) => (
              <div key={lane.i} onMouseEnter={() => this.setState({ hoverLane: lane.i })} onMouseLeave={() => this.setState({ hoverLane: null })} style={{ height: G.laneH, display: 'flex', alignItems: 'center', gap: 14, borderBottom: '1px solid var(--line-1)', opacity: st.hoverLane == null || st.hoverLane === lane.i ? 1 : 0.5, transition: 'opacity .2s' }}>
                <div style={{ width: 132, flex: 'none', display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ display: 'inline-flex', width: 34, height: 34, flex: 'none', alignItems: 'center', justifyContent: 'center', background: 'var(--paper-sink)', border: `1px solid ${lane.discBd}`, fontFamily: 'var(--font-serif)', fontSize: 17, color: 'var(--ink-0)', transition: 'border-color .3s' }}>{lane.greek}</span>
                  <div>
                    <div style={{ fontFamily: 'var(--font-serif)', fontSize: 13, fontWeight: 600, letterSpacing: 1, color: 'var(--ink-0)' }}>{lane.name}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ink-3)', marginTop: 1 }}>{lane.gloss} · {lane.count}节点 · 热{lane.heatTxt}</div>
                  </div>
                </div>
                <div style={{ flex: 1, display: 'flex', flexWrap: 'wrap', gap: '8px 6px', alignContent: 'center' }}>
                  {lane.items.map((it) => (
                    <span key={it.s.id} data-hv="soft" onClick={(e) => this.openSeg(it.s.id, e)} title={`${it.s.name} · 国内站位 ${it.effPos}`} style={{ position: 'relative', zIndex: it.mig ? 6 : 0, display: 'inline-flex', alignItems: 'center', gap: 6, border: `1px solid ${it.bd}`, borderLeftWidth: 3, background: 'var(--paper-1)', padding: '4px 9px', cursor: 'pointer', whiteSpace: 'nowrap', outline: it.mig ? '2px solid var(--zhu)' : 'none', outlineOffset: 2, boxShadow: it.mig ? '3px 6px 0 rgba(178,58,43,0.20)' : 'none', transform: it.mig ? 'translateY(-5px)' : 'none', transition: 'transform .5s cubic-bezier(.22,.8,.26,1), box-shadow .3s' }}>
                      <span style={{ fontSize: 11.5, color: 'var(--ink-0)', letterSpacing: .3 }}>{it.s.name}</span>
                      {it.market ? (
                        <span style={{ fontSize: 8.5, color: 'var(--ink-4)', fontFamily: 'var(--font-serif)', border: '1px solid var(--line-2)', lineHeight: '12px', padding: '0 3px' }} title="国内市场型">市</span>
                      ) : (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>{it.pips.map((pp, k) => <i key={k} style={{ width: 5, height: 5, borderRadius: '50%', display: 'block', background: pp.bg, transform: pp.tf, transition: 'background .45s ease, transform .45s cubic-bezier(.3,1.3,.5,1)' }} />)}</span>
                      )}
                      <span style={{ fontSize: 9, color: 'var(--zhu)', fontFamily: 'var(--font-mono)' }}>{it.dualTxt}</span>
                      <span style={{ color: 'var(--jin)', fontSize: 10 }}>{it.star}</span>
                      {it.mig && <span style={{ position: 'absolute', left: '50%', bottom: '100%', marginBottom: 5, whiteSpace: 'nowrap', background: 'var(--zhu)', color: 'var(--text-on-zhu)', fontSize: 9, fontFamily: 'var(--font-mono)', letterSpacing: .5, padding: '2px 7px', boxShadow: '0 4px 10px rgba(33,27,18,0.22)', animation: 'glMigFlag 3.4s ease-out forwards', pointerEvents: 'none' }}>研报 · {it.mig.note} · {it.mig.from}→{it.mig.to}</span>}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div style={{ padding: '16px 2px 0', fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.8, maxWidth: 1040 }}>
          读法:五轴由上而下 <b style={{ color: 'var(--ink-1)', fontWeight: 500 }}>确定性 → 弹性</b>——β 全球需求(业绩兑现型)最确定,Ψ 映射主题最主题化。每个环节标注国内站位(领先→并跑→追赶→短板→国内市场);<b style={{ color: 'var(--zhu)', fontWeight: 500 }}>朱边 Δ+Ω</b> 为涨价周期叠加国产替代的双击环节,历史上十倍股多诞生于此。节点内四点为国内站位(短板·追赶·并跑·领先),研报抽到国产化率/认证/份额修正时,对应节点<b style={{ color: 'var(--zhu)', fontWeight: 500 }}>站位沿轴迁移</b>(点「演示坐标修正」为示意动画);轴的粗细随该逻辑当前热度呼吸,地球上的「中」随转动掠过正面——均为示意。
        </div>
      </div>
    );
  }

  // ── 环节明细 ──
  _renderDetail() {
    const st = this.state, sid = st.sel, s = this._seg(sid);
    if (!s) return null;
    const q = QUAD[s.quad] || QUAD.ll, g = this.GROUPS.find((x) => x.id === s.g) || {};
    const det = st.detail && st.detail.ok ? st.detail : null;
    const edges = this.EDGES.filter((e) => e.from.includes(sid) || e.to.includes(sid)).map((e) => {
      const out = e.from.includes(sid), others = (out ? e.to : e.from).filter((x) => x !== sid), goId = others.find((x) => x[0] !== 'D'), neg = e.sign === '-';
      return { id: e.id, sign: e.sign, sc: neg ? 'var(--dai)' : 'var(--zhu)', dir: out ? '传出 →' : '← 传入', route: e.from.map((x) => this._nodeName(x)).join('、') + ' → ' + e.to.map((x) => this._nodeName(x)).join('、'), mech: e.mech, lag: e.lag, valid: e.valid, goId, goName: goId ? this._nodeName(goId) : '' };
    });
    const dnarrs = this.NARRS.filter((n) => n.act[sid] != null).map((n) => ({ ...n, ...narrView(n), w: n.act[sid].toFixed(1) }));
    const ops = det ? (det.opinions || []) : [];
    const rows = det ? (det.stock_rows || []) : [];
    const momTxt = s.mom == null ? '—' : (s.mom >= 0 ? '+' : '') + s.mom + '%';
    const cell = (label, val, color) => <div><div style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: 2, fontFamily: 'var(--font-serif)' }}>{label}</div><div style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 20, color: color || 'var(--ink-0)' }}>{val}</div></div>;
    const panelHead = (t, extra) => <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 13px', borderBottom: '1px solid var(--line-1)' }}><span style={{ fontFamily: 'var(--font-serif)', fontSize: 12.5, fontWeight: 600, letterSpacing: 2, color: 'var(--ink-0)' }}>{t}</span>{extra}</div>;
    return (
      <div style={{ animation: 'glRise .45s both' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 22px 12px', borderBottom: '1px solid var(--line-1)', background: 'var(--paper-0)' }}>
          <span onClick={() => this.goBack()} data-hv="back" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, border: '1px solid var(--line-2)', background: 'var(--paper-1)', padding: '6px 14px', fontSize: 12, letterSpacing: 2, color: 'var(--ink-1)', cursor: 'pointer' }}>‹ 返回河图</span>
          <span style={{ display: 'inline-flex', width: 38, height: 38, alignItems: 'center', justifyContent: 'center', background: 'var(--zhu)', color: 'var(--text-on-zhu)', fontFamily: 'var(--font-serif)', fontSize: 19, borderRadius: 3 }}>{s.name[0]}</span>
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span style={{ fontFamily: 'var(--font-serif)', fontSize: 24, fontWeight: 600, letterSpacing: 3, color: 'var(--ink-0)' }}>{s.name}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-3)' }}>{s.id}</span>
              <span style={{ color: 'var(--jin)', fontSize: 13 }}>{'★'.repeat(s.stars)}</span>
            </div>
            <div style={{ marginTop: 2, fontSize: 11, color: 'var(--ink-2)', letterSpacing: .5 }}>{g.name} · {s.prospect}</div>
          </div>
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-2)', border: '1px solid var(--line-1)', background: 'var(--paper-2)', padding: '4px 10px' }}>主逻辑 <b style={{ color: 'var(--jin)' }}>{s.eq}</b></span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, padding: '4px 10px', border: `1px solid ${q.bd}`, color: q.fg, background: q.bg, letterSpacing: 1 }}>{q.label}</span>
          {!det && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>载入…</span>}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '370px 1fr 396px', gap: 16, padding: '16px 22px 28px', alignItems: 'start' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ border: '1px solid var(--line-2)', background: 'var(--paper-1)' }}>
              {panelHead('环节逻辑')}
              <div style={{ padding: '12px 14px 10px', fontFamily: 'var(--font-serif)', fontSize: 14, lineHeight: 1.8, color: 'var(--ink-1)' }}>{s.logic}</div>
              <div style={{ padding: '0 14px 13px', display: 'flex', flexWrap: 'wrap', gap: 6 }}>{(s.kw || []).map((k, i) => <span key={i} style={{ border: '1px solid var(--line-1)', color: 'var(--ink-2)', fontSize: 10.5, padding: '2px 8px', background: 'var(--paper-2)' }}>{k}</span>)}</div>
            </div>
            <div style={{ border: '1px solid var(--line-2)', background: 'var(--paper-1)' }}>
              {panelHead('全球坐标', <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--jin)' }}>{s.eq}</span>)}
              <dl style={{ display: 'grid', gridTemplateColumns: '36px 1fr', gap: '5px 12px', margin: 0, padding: '12px 14px 13px', fontSize: 11.5 }}>
                <dt style={{ color: 'var(--ink-3)', letterSpacing: 2, fontFamily: 'var(--font-serif)' }}>国际</dt><dd style={{ margin: 0, color: 'var(--ink-1)', lineHeight: 1.6 }}>{s.intl}</dd>
                <dt style={{ color: 'var(--ink-3)', letterSpacing: 2, fontFamily: 'var(--font-serif)' }}>国内</dt><dd style={{ margin: 0, lineHeight: 1.6 }}><span style={{ color: 'var(--zhu)', fontWeight: 500 }}>{s.cn}</span></dd>
                <dt style={{ color: 'var(--ink-3)', letterSpacing: 2, fontFamily: 'var(--font-serif)' }}>壁垒</dt><dd style={{ margin: 0, color: 'var(--ink-1)', lineHeight: 1.6 }}>{s.moat}</dd>
                <dt style={{ color: 'var(--ink-3)', letterSpacing: 2, fontFamily: 'var(--font-serif)' }}>前景</dt><dd style={{ margin: 0, color: 'var(--ink-1)', lineHeight: 1.6 }}>{s.prospect}</dd>
              </dl>
            </div>
            <div style={{ border: '1px solid var(--line-2)', background: 'var(--paper-1)' }}>
              {panelHead('关联叙事', <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--ink-3)' }}>激活权重</span>)}
              <div style={{ padding: '10px 14px 12px', display: 'flex', flexDirection: 'column', gap: 9 }}>
                {dnarrs.length === 0 && <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>无关联叙事</div>}
                {dnarrs.map((dn) => (
                  <div key={dn.id} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                    <span style={{ fontFamily: 'var(--font-serif)', fontSize: 12, fontWeight: 600, color: 'var(--ink-0)', whiteSpace: 'nowrap' }}>{dn.name}</span>
                    <span style={{ fontSize: 9, padding: '0 5px', border: `1px solid ${dn.stBd}`, color: dn.stFg, background: dn.stBg, whiteSpace: 'nowrap' }}>{dn.status}</span>
                    <span style={{ flex: 1, height: 3, background: 'var(--line-1)', position: 'relative' }}><i style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: (dn.temp == null ? 0 : dn.temp) + '%', background: dn.barC }} /></span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-2)' }}>{dn.temp == null ? '—' : dn.temp + '°'} · w{dn.w}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ border: '1px solid var(--line-2)', background: 'var(--paper-1)' }}>
              {panelHead('传导逻辑', <><span style={{ fontSize: 10, color: 'var(--ink-3)' }}>框架内与本环节相连的传导边 · 点击对端环节可跳转</span><span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>{edges.length} 条</span></>)}
              <div style={{ padding: '10px 14px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                {edges.map((ed) => (
                  <div key={ed.id} style={{ border: '1px solid var(--line-1)', background: 'var(--paper-2)', padding: '8px 11px' }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: ed.sc, border: `1px solid ${ed.sc}`, padding: '0 5px', lineHeight: '16px' }}>{ed.id} {ed.sign}</span>
                      <span style={{ fontSize: 11, color: 'var(--ink-3)', letterSpacing: 1 }}>{ed.dir}</span>
                      <span style={{ fontSize: 12, color: 'var(--ink-1)' }}>{ed.route}</span>
                      {ed.goId && <span onClick={() => this.jump(ed.goId)} style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--qing)', cursor: 'pointer', whiteSpace: 'nowrap' }}>{ed.goName} →</span>}
                    </div>
                    <div style={{ marginTop: 5, fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.6 }}>{ed.mech}</div>
                    <div style={{ marginTop: 5, display: 'flex', gap: 8, alignItems: 'center', fontSize: 9.5, color: 'var(--ink-3)', flexWrap: 'wrap' }}>
                      <span style={{ border: '1px solid var(--line-1)', padding: '0 5px', lineHeight: '15px' }}>时滞 {ed.lag}</span>
                      <span style={{ fontFamily: 'var(--font-mono)' }}>验证:{ed.valid}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            {(() => {
              const dps = det ? (det.datapoints || []) : [];
              if (!dps.length) return null;
              return (
                <div style={{ border: '1px solid var(--line-2)', background: 'var(--paper-1)' }}>
                  {panelHead('量化数据点', <><span style={{ fontSize: 10, color: 'var(--ink-3)' }}>研报硬数字 · 显式挂靠本环节 · 近30日</span><span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>{dps.length} 条</span></>)}
                  <div style={{ padding: '8px 14px 10px', display: 'flex', flexDirection: 'column', gap: 5 }}>
                    {dps.slice(0, 12).map((dp, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 11, borderBottom: i < Math.min(dps.length, 12) - 1 ? '1px solid var(--line-1)' : 'none', paddingBottom: 5 }}>
                        <span style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line-1)', padding: '0 4px', flex: 'none' }}>{dp.kind}</span>
                        <span style={{ color: 'var(--ink-1)' }}>{dp.subject}</span>
                        <b style={{ fontFamily: 'var(--font-mono)', color: 'var(--zhu)' }}>{dp.value}</b>
                        {dp.period && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ink-3)' }}>{dp.period}</span>}
                        {dp.edge_id && <span style={{ fontSize: 9, color: 'var(--dai)', border: '1px solid var(--dai-soft)', padding: '0 4px' }} title="验证该传导边">{dp.edge_id}</span>}
                        <span style={{ marginLeft: 'auto', fontSize: 9.5, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>{dp.org} · {(dp.publish_ts || '').slice(5, 10)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}
            <div style={{ border: '1px solid var(--line-2)', background: 'var(--paper-1)' }}>
              {panelHead('研报观点流', <><span style={{ fontSize: 10, color: 'var(--ink-3)' }}>近30日 · 半衰7天</span><span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>{ops.length} 篇</span></>)}
              {ops.length > 0 ? (
                <div style={{ padding: '10px 14px 12px', display: 'flex', flexDirection: 'column', gap: 9 }}>
                  {ops.map((op, i) => {
                    const bull = op.stance === '多', bear = op.stance === '空';
                    return (
                      <div key={i} style={{ border: '1px solid var(--line-1)', background: 'var(--paper-2)', padding: '9px 12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 10.5, color: 'var(--ink-3)' }}>
                          <span style={{ fontFamily: 'var(--font-serif)', fontSize: 10.5, padding: '0 6px', lineHeight: '17px', letterSpacing: 1, background: bull ? 'var(--zhu)' : bear ? 'var(--dai)' : 'transparent', color: bull || bear ? 'var(--text-on-zhu)' : 'var(--ink-2)', border: `1px solid ${bull ? 'var(--zhu)' : bear ? 'var(--dai)' : 'var(--line-2)'}` }}>{op.stance || '中'}</span>
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--jin)', letterSpacing: 1 }}>{'●'.repeat(op.strength || 1) + '○'.repeat(Math.max(0, 3 - (op.strength || 1)))}</span>
                          {op.rating_change && op.rating_change !== '无' && <span style={{ fontSize: 9, color: op.rating_change === '下调' ? 'var(--dai)' : 'var(--zhu)', border: `1px solid ${op.rating_change === '下调' ? 'var(--dai-soft)' : 'var(--zhu-soft)'}`, padding: '0 4px', whiteSpace: 'nowrap' }}>{op.rating_change}{op.rating ? '·' + op.rating : ''}</span>}
                          {op.target_price != null && <span style={{ fontSize: 9, fontFamily: 'var(--font-mono)', color: 'var(--ink-2)', border: '1px solid var(--line-1)', padding: '0 4px', whiteSpace: 'nowrap' }}>TP {op.target_price}</span>}
                          <span style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}>{op.org} · {(op.publish_ts || '').slice(5, 10)}</span>
                        </div>
                        <div style={{ marginTop: 5, fontSize: 12, color: 'var(--ink-1)', fontWeight: 500, lineHeight: 1.5 }}>{op.title}</div>
                        {op.quote && <div style={{ margin: '7px 0 2px', paddingLeft: 9, borderLeft: '2px solid var(--jin)', fontFamily: 'var(--font-serif)', fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.75 }}>「{op.quote}」</div>}
                        {op.quote_dropped && <div style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>引句未过原文校验,已省略</div>}
                        <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', fontSize: 9.5, color: 'var(--ink-3)', flexWrap: 'wrap' }}><span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>{op.doc_id}</span></div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div style={{ padding: '26px 14px 28px', textAlign: 'center' }}>
                  <div style={{ fontFamily: 'var(--font-serif)', fontSize: 13, color: 'var(--ink-2)', letterSpacing: 1 }}>该环节暂无已抽取观点(不编造)</div>
                  <div style={{ marginTop: 6, fontSize: 10.5, color: 'var(--ink-3)' }}>处理研报后此处展示近30日观点流 · 引句须过原文校验</div>
                </div>
              )}
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ border: '1px solid var(--line-2)', background: 'var(--paper-1)' }}>
              {panelHead('双轴读数', <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--ink-3)' }}>{det ? '' : '载入…'}</span>)}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', padding: '12px 14px 6px', gap: 10 }}>
                {cell('动量20日', momTxt, momColor(s.mom))}
                {cell('研报景气', s.n30 != null && s.n30 > 0 ? '+' + s.n30 : '—')}
                {cell('行情温度', s.therm == null ? '—' : s.therm + '°')}
              </div>
              {(() => {
                const rs = (det && det.research) || {};
                const chip = (label, up, dn) => (
                  <span style={{ fontSize: 10, color: 'var(--ink-2)', border: '1px solid var(--line-1)', background: 'var(--paper-2)', padding: '2px 7px', whiteSpace: 'nowrap' }}>
                    {label} <b style={{ fontFamily: 'var(--font-mono)', color: up > 0 ? 'var(--zhu)' : 'var(--ink-3)' }}>↑{up == null ? '—' : up}</b>
                    <b style={{ fontFamily: 'var(--font-mono)', color: dn > 0 ? 'var(--dai)' : 'var(--ink-3)', marginLeft: 4 }}>↓{dn == null ? '—' : dn}</b>
                  </span>
                );
                return (
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '0 14px 13px' }}>
                    {chip('评级', rs.rating_up, rs.rating_dn)}
                    {chip('盈利修正', rs.fc_up, rs.fc_dn)}
                    <span style={{ fontSize: 10, color: 'var(--ink-2)', border: '1px solid var(--line-1)', background: 'var(--paper-2)', padding: '2px 7px' }}>覆盖机构 <b style={{ fontFamily: 'var(--font-mono)' }}>{rs.n_orgs == null ? '—' : rs.n_orgs}</b></span>
                  </div>
                );
              })()}
            </div>
            <div style={{ border: '1px solid var(--line-2)', background: 'var(--paper-1)', display: 'flex', flexDirection: 'column' }}>
              {panelHead('票池', <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>{rows.length} 只</span>)}
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
                <thead><tr>
                  {['个股', '现价', '当日', '资金5日', 'v4分位'].map((h, i) => <th key={i} style={{ fontWeight: 400, fontSize: 9.5, color: 'var(--ink-3)', textAlign: i === 0 ? 'left' : 'right', padding: i === 0 ? '7px 6px 5px 14px' : '7px 6px 5px', borderBottom: '1px solid var(--line-2)', letterSpacing: 1, background: 'var(--paper-sink)' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.length === 0 && <tr><td colSpan={5} style={{ padding: '18px 14px', textAlign: 'center', color: 'var(--ink-3)', fontSize: 11 }}>{det ? '票池为空' : '载入…'}</td></tr>}
                  {rows.map((r) => (
                    <tr key={r.code} data-hv="row">
                      <td style={{ padding: '7px 6px 7px 14px', borderBottom: '1px solid var(--line-1)', color: 'var(--ink-1)' }}>{r.name}<span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--ink-3)', marginLeft: 5 }}>{(r.code || '').slice(2)}</span>{r.role === 'anchor' && <span style={{ color: 'var(--zhu)', fontSize: 8.5, border: '1px solid var(--zhu-soft)', padding: '0 3px', marginLeft: 5 }}>锚</span>}{r.note && <span style={{ color: 'var(--jin)', fontSize: 8.5, border: '1px solid var(--jin-soft)', padding: '0 3px', marginLeft: 4 }} title={r.note}>核</span>}</td>
                      <td style={{ padding: '7px 6px', borderBottom: '1px solid var(--line-1)', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-1)' }}>{r.px == null ? '—' : r.px}</td>
                      <td style={{ padding: '7px 6px', borderBottom: '1px solid var(--line-1)', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11, color: momColor(r.chg) }}>{r.chg == null ? '—' : (r.chg >= 0 ? '+' : '') + r.chg + '%'}</td>
                      <td style={{ padding: '7px 6px', borderBottom: '1px solid var(--line-1)', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11, color: momColor(r.ff5) }}>{r.ff5 == null ? '—' : (r.ff5 >= 0 ? '+' : '') + r.ff5 + '亿'}</td>
                      <td style={{ padding: '7px 14px 7px 6px', borderBottom: '1px solid var(--line-1)', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-1)' }}>{r.v4pct == null ? '—' : r.v4pct}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ padding: '11px 14px 13px', display: 'flex', gap: 8 }}>
                <span data-hv="chip" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', fontSize: 11, border: '1px solid var(--line-2)', color: 'var(--ink-2)', background: 'var(--paper-2)', cursor: 'pointer' }} onClick={() => { location.href = '../screen/观澜 · 选股.html'; }}>↗ 送去选股页对比</span>
                <span data-hv="chip" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', fontSize: 11, border: '1px solid var(--line-2)', color: 'var(--ink-2)', background: 'var(--paper-2)', cursor: 'pointer' }} onClick={() => { location.href = '../luozi/观澜 · 落子.html'; }}>↗ 落子盯盘</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  _renderZoom() {
    const z = this.state.zoom;
    if (!z || !this._rect) return null;
    const zs = this._seg(z.sid); if (!zs) return null;
    const big = z.big;
    return (
      <div style={{ position: 'fixed', zIndex: 200, left: big ? 0 : Math.round(this._rect.x), top: big ? 0 : Math.round(this._rect.y), width: big ? window.innerWidth : Math.round(this._rect.w), height: big ? window.innerHeight : Math.round(this._rect.h), background: 'var(--paper-1)', border: '1px solid var(--line-2)', boxShadow: '0 10px 34px rgba(33,27,18,0.20)', transition: `all ${this._zms()}ms cubic-bezier(.22,.8,.26,1)`, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, opacity: big ? 1 : 0, transition: 'opacity 220ms ease' }}>
          <span style={{ display: 'inline-flex', width: 46, height: 46, alignItems: 'center', justifyContent: 'center', background: 'var(--zhu)', color: 'var(--text-on-zhu)', fontFamily: 'var(--font-serif)', fontSize: 23, borderRadius: 3 }}>{zs.name[0]}</span>
          <span style={{ fontFamily: 'var(--font-serif)', fontSize: 32, fontWeight: 600, letterSpacing: 5, color: 'var(--ink-0)' }}>{zs.name}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--ink-3)' }}>{zs.id} · {zs.eq}</span>
        </div>
      </div>
    );
  }

  _renderFoot(corpusOk) {
    const note = corpusOk ? '' : '(语料未就绪,研报侧显 — 不编数)';
    return (
      <div style={{ padding: '8px 22px 22px', fontSize: 10, color: 'var(--ink-3)', display: 'flex', gap: 14, alignItems: 'center' }}>
        <span style={{ fontFamily: 'var(--font-serif)', border: '1.5px solid var(--ink-2)', color: 'var(--ink-2)', padding: '1px 7px', letterSpacing: 3, fontSize: 10.5 }}>觀瀾</span>
        <span>行情/动量/温度/票池 = 引擎实时聚合 · 研报观点/景气 = Kimi 抽取{note} · 驱动读数为人工维护框架标注 · 缺失显 — 不编数</span>
        <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>{this.state.fw} v{((this.state.board || {}).meta || {}).version || 1}</span>
      </div>
    );
  }
}
ReactDOM.createRoot(document.getElementById('root')).render(<River />);
