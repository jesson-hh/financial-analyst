// 观澜 · 全局模块导航条 guanlan-nav.js
// 顶部统一一栏切换九个主模块,高亮当前页。flex 列布局把内容下压,兼容内部 height:100vh。
(function () {
  // 帷幄嵌入卫生:?embed=1 时本页被装进帷幄右栏 iframe → 不注入导航条与 body/#root 强样式
  if (new URLSearchParams(location.search).get('embed') === '1') return;
  var MODULES = [
    { label: '帷幄', file: '../console/观澜 · 帷幄.html', home: true },
    { label: '席位 · 落子', file: '../seats/观澜 · 落子.html' },
    { label: '选股', file: '../screen/观澜 · 选股.html' },
    { label: '工作流', file: '../factor/观澜 · AI 工作流.html' },
    { label: 'AI投研', file: '../industry/观澜 · AI投研.html' },
    { label: '全球情绪', file: '../macro/观澜 · 全球情绪.html' },
    { label: '资金流向', file: '../fundflow/观澜 · 资金流向.html' },
    { label: '海外', file: '../overseas/观澜 · 海外.html' },
    { label: '资讯', file: '../news/观澜 · 资讯.html' },
  ];
  // ── 桌面壳分支 ────────────────────────────────────────────────────
  // 顶栏被十二个页面共用,不是九个:MODULES 只列了 9 个 tab,但
  // cards/chat/graph 三个页面仍在加载这同一份 guanlan-nav.js(2026-06-12
  // phase2 收敛时"隐藏不删"——从 tab 条隐去,<script> 标签留着)。浏览器里也吃
  // 同一份 —— 页面越多,这段没条件化的代价就越大,所以这段必须条件化:浏览器里
  // window.pywebview 不存在,glDesktopApi() 返回 null,一切照旧。
  // 唯一信号是 window.pywebview.api.open_window 是否是一个真的可调用函数 ——
  // 这正是这条分支真正需要的东西(一座能开窗的桥),不是"看起来像桌面"就够;
  // 只在点击那一刻读,与 pywebview 自己注入 window.pywebview 的时机没有竞态。
  // shell.py 另外会在 loaded 时注入 window.GL_DESKTOP,那是给未来换壳用的
  // 可移植性标记 —— 本文件刻意不读它:没有真桥撑着,一个标记开不了窗。
  function glDesktopApi() {
    var api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.open_window !== 'function') return null;
    return api;
  }
  function glWantsNewWindow(e) {
    return e.button === 1 || e.ctrlKey || e.metaKey;   // 中键 / Ctrl / Cmd
  }
  function glOnActivate(e) {
    if (!glWantsNewWindow(e)) return;                  // 普通左键 → 原地跳转
    var api = glDesktopApi();
    if (!api) return;                                  // 桥不在 → 不拦截,退回普通跳转
    e.preventDefault();
    try {
      api.open_window(new URL(e.currentTarget.getAttribute('href'), location.href).href);
    } catch (err) { /* 开窗失败不该弄坏顶栏 */ }
  }
  function glBindNewWindow(a) {
    a.addEventListener('click', glOnActivate);
    a.addEventListener('auxclick', glOnActivate);      // click 不为中键触发
  }

  var here = '';
  try { here = decodeURIComponent(location.pathname.split('/').pop() || ''); } catch (e) { here = location.pathname; }

  var st = document.createElement('style');
  st.textContent =
    'body{margin:0!important;display:flex!important;flex-direction:column!important;min-height:100vh;}' +
    '#gl-nav{flex:0 0 44px;display:flex;align-items:stretch;height:44px;padding:0 18px;box-sizing:border-box;' +
    'background:linear-gradient(180deg,var(--paper,#f1ead9),var(--paper-2,#ebe2cc));' +
    'border-bottom:1px solid var(--line,#cfc4ab);box-shadow:0 1px 0 rgba(255,255,255,.4) inset, 0 2px 8px rgba(28,24,20,0.04);' +
    'position:sticky;top:0;z-index:9000;font-family:var(--sans),sans-serif;white-space:nowrap;user-select:none;}' +
    '#gl-brand{display:flex;align-items:center;gap:9px;padding-right:16px;margin-right:4px;}' +
    '#gl-brand .s{width:23px;height:23px;background:var(--yin,#a8392d);color:var(--paper,#f1ead9);font-family:var(--serif),serif;' +
    'font-size:14px;display:flex;align-items:center;justify-content:center;border-radius:2px;}' +
    '#gl-brand .w{font-family:var(--serif),serif;font-size:15px;font-weight:600;color:var(--ink,#1c1814);letter-spacing:.14em;}' +
    '#gl-nav .sep{align-self:center;width:1px;height:20px;background:var(--line,#cfc4ab);margin-right:6px;}' +
    '.gl-tab{display:inline-flex;align-items:center;height:44px;padding:0 17px;font-family:var(--serif),serif;font-size:13.5px;' +
    'color:var(--ink-3,#9e9482);text-decoration:none;letter-spacing:.04em;border-bottom:2px solid transparent;' +
    'margin-bottom:-1px;transition:color .15s;cursor:pointer;}' +
    '.gl-tab:hover{color:var(--ink-1,#3a342c);}' +
    '.gl-tab.on{color:var(--ink,#1c1814);font-weight:600;border-bottom-color:var(--yin,#a8392d);}' +
    '#gl-alert{margin-left:auto;align-self:center;height:27px;display:flex;align-items:center;gap:6px;padding:0 9px;' +
    'border:1px solid var(--line,#cfc4ab);border-radius:3px;background:rgba(255,255,255,.18);color:var(--ink-3,#9e9482);' +
    'font:11px var(--mono),monospace;cursor:pointer;transition:.16s;}' +
    '#gl-alert .lamp{width:7px;height:7px;border-radius:50%;background:#999080;}' +
    '#gl-alert.hot{border-color:#b33127;background:#b33127;color:#fff;box-shadow:0 0 0 3px rgba(179,49,39,.12);}' +
    '#gl-alert.hot .lamp{background:#fff;animation:glPulse 1.35s infinite;}' +
    '#gl-alert.running .lamp{background:var(--dai,#4a6b5c);}' +
    '#gl-right{margin-left:0;align-self:center;display:flex;align-items:center;gap:7px;' +
    'font-family:var(--mono),monospace;font-size:10px;color:var(--ink-3,#9e9482);letter-spacing:.04em;cursor:pointer;}' +
    '#gl-right .dot{width:6px;height:6px;border-radius:50%;background:var(--dai,#4a6b5c);}' +
    '#gl-alert-panel{position:fixed;z-index:9300;top:49px;right:18px;width:min(360px,calc(100vw - 28px));max-height:430px;' +
    'overflow:auto;box-sizing:border-box;background:var(--paper,#f1ead9);border:1px solid var(--line,#cfc4ab);' +
    'box-shadow:0 12px 35px rgba(28,24,20,.22);padding:13px;display:none;font-family:var(--sans),sans-serif;}' +
    '#gl-alert-panel.open{display:block;}#gl-alert-panel .hd{display:flex;align-items:center;justify-content:space-between;' +
    'font:600 14px var(--serif),serif;color:var(--ink,#1c1814);margin-bottom:8px;}' +
    '#gl-alert-panel .meta{font:10px var(--mono),monospace;color:var(--ink-3,#9e9482);margin-bottom:10px;line-height:1.55;}' +
    '#gl-alert-panel .evt{border-top:1px solid var(--line,#cfc4ab);padding:9px 2px;color:var(--ink-1,#3a342c);font-size:12px;line-height:1.5;}' +
    '#gl-alert-panel .evt.red{border-left:3px solid #b33127;padding-left:8px;background:rgba(179,49,39,.045);}' +
    '#gl-alert-panel .evt time{display:block;color:var(--ink-3,#9e9482);font:9px var(--mono),monospace;margin-top:3px;}' +
    '#gl-alert-panel .empty{padding:22px 4px;text-align:center;color:var(--ink-3,#9e9482);font-size:12px;}' +
    '#gl-alert-toast{position:fixed;z-index:9400;right:18px;top:56px;width:min(380px,calc(100vw - 28px));box-sizing:border-box;' +
    'background:#a82f26;color:#fff;border:1px solid #7d211b;box-shadow:0 12px 35px rgba(105,22,17,.34);padding:13px 38px 13px 14px;' +
    'font:13px/1.55 var(--sans),sans-serif;display:none;}#gl-alert-toast.show{display:block;animation:glDrop .2s ease-out;}' +
    '#gl-alert-toast b{display:block;font:600 15px var(--serif),serif;margin-bottom:3px;}#gl-alert-toast button{position:absolute;right:8px;top:7px;' +
    'border:0;background:transparent;color:#fff;font-size:18px;cursor:pointer;}' +
    '@keyframes glPulse{50%{opacity:.28;transform:scale(.72)}}@keyframes glDrop{from{opacity:0;transform:translateY(-8px)}}' +
    '@media(max-width:1180px){#gl-right{display:none}.gl-tab{padding-left:10px;padding-right:10px}}' +
    '#root{flex:1 1 auto!important;height:auto!important;min-height:0!important;}' +
    '#root>*{height:100%!important;}';
  document.head.appendChild(st);

  var bar = document.createElement('div');
  bar.id = 'gl-nav';
  bar.innerHTML =
    '<a id="gl-brand" href="../graph/观澜 · 研究图谱.html" style="text-decoration:none"><span class="s">觀</span><span class="w">觀瀾</span></a>' +
    '<span class="sep"></span>';

  var brand = bar.querySelector('#gl-brand');
  if (brand) glBindNewWindow(brand);

  MODULES.forEach(function (m) {
    var on = here === m.file.split('/').pop() || (here === '' && m.home);
    var a = document.createElement('a');
    a.className = 'gl-tab' + (on ? ' on' : '');
    a.href = m.file;
    a.textContent = m.label;
    glBindNewWindow(a);
    bar.appendChild(a);
  });

  // 全局预警中心：服务端常驻执行，顶栏只负责展示。规则配置由帷幄的确认门完成。
  var alertBtn = document.createElement('button');
  alertBtn.id = 'gl-alert';
  alertBtn.type = 'button';
  alertBtn.title = '全局预警中心（点击展开）';
  alertBtn.innerHTML = '<span class="lamp"></span><span class="txt">预警 · —</span>';
  bar.appendChild(alertBtn);

  var alertPanel = document.createElement('div');
  alertPanel.id = 'gl-alert-panel';
  alertPanel.innerHTML = '<div class="hd"><span>全局预警中心</span><span style="font:10px var(--mono),monospace;color:var(--ink-3)">后台确定性执行</span></div>' +
    '<div class="meta">正在读取规则与交易时段…</div><div class="items"><div class="empty">暂无触发事件</div></div>';
  var alertToast = document.createElement('div');
  alertToast.id = 'gl-alert-toast';
  alertToast.innerHTML = '<button type="button" aria-label="关闭">×</button><b></b><span></span>';
  var alertState = { status: null, events: [], lastToast: '', open: false };
  var seenKey = 'guanlan.watch-alert.seen.v1';

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }
  function seenId() { try { return localStorage.getItem(seenKey) || ''; } catch (e) { return ''; } }
  function markSeen() {
    if (!alertState.events.length) return;
    try { localStorage.setItem(seenKey, alertState.events[0].id || ''); } catch (e) {}
    renderAlerts();
  }
  function phaseText(s) {
    var p = s && s.session && s.session.market_phase;
    return ({open:'交易中',preopen:'盘前',lunch_break:'午间休市',closed:'已收盘',non_trading_day:'休市'})[p] || '状态未知';
  }
  function renderAlerts() {
    var status = alertState.status || {};
    var unseen = 0, marker = seenId();
    for (var i = 0; i < alertState.events.length; i++) {
      if (alertState.events[i].id === marker) break;
      unseen++;
    }
    alertBtn.className = unseen ? 'hot' : ((status.session && status.session.market_open) ? 'running' : '');
    alertBtn.querySelector('.txt').textContent = unseen ? ('红色预警 · ' + unseen) : ('预警 · ' + (status.rules_enabled || 0));
    var meta = alertPanel.querySelector('.meta');
    meta.textContent = phaseText(status) + ' · 已启用 ' + (status.rules_enabled || 0) + ' 条 / 共 ' + (status.rules_total || 0) +
      ' 条' + (status.last_tick && status.last_tick.at ? ' · 上次扫描 ' + status.last_tick.at.replace('T',' ').slice(5,19) : '');
    var items = alertPanel.querySelector('.items');
    if (!alertState.events.length) {
      items.innerHTML = '<div class="empty">暂无触发事件。可在帷幄中让 Agent 研究并设置预警。</div>';
    } else {
      items.innerHTML = alertState.events.map(function (e) {
        return '<div class="evt red"><strong>' + esc(e.rule_name || e.title || '预警') + '</strong><br>' +
          esc(e.message || '') + '<time>' + esc((e.fired_at || '').replace('T',' ')) + '</time></div>';
      }).join('');
    }
    if (unseen && alertState.events[0].id !== alertState.lastToast) {
      var latest = alertState.events[0];
      alertState.lastToast = latest.id;
      alertToast.querySelector('b').textContent = latest.title || '红色预警';
      alertToast.querySelector('span').textContent = latest.message || '';
      alertToast.classList.add('show');
    }
  }
  function refreshAlerts() {
    Promise.all([
      fetch('/watch-alerts/status', {cache:'no-store'}).then(function (r) { return r.json(); }),
      fetch('/watch-alerts/events?limit=10', {cache:'no-store'}).then(function (r) { return r.json(); })
    ]).then(function (rows) {
      alertState.status = rows[0] || {};
      alertState.events = (rows[1] && rows[1].events) || [];
      renderAlerts();
    }).catch(function () {
      alertBtn.className = '';
      alertBtn.querySelector('.txt').textContent = '预警 · 离线';
      alertPanel.querySelector('.meta').textContent = '预警服务暂不可达；未把离线状态冒充为正在盯盘。';
    });
  }
  alertBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    alertState.open = !alertState.open;
    alertPanel.classList.toggle('open', alertState.open);
    if (alertState.open) markSeen();
  });
  alertPanel.addEventListener('click', function (e) { e.stopPropagation(); });
  alertToast.querySelector('button').addEventListener('click', function () {
    alertToast.classList.remove('show'); markSeen();
  });
  document.addEventListener('click', function () { alertState.open = false; alertPanel.classList.remove('open'); });

  // 右:共享档案库物料数(读 GL,带轻动效)
  var right = document.createElement('a');
  right.id = 'gl-right';
  right.href = '../graph/观澜 · 研究图谱.html';
  right.style.textDecoration = 'none';
  var total = 0;
  try { total = (window.GL && GL.stats && GL.stats().total) || 0; } catch (e) {}
  right.innerHTML = '<span class="dot"></span>共享档案库 · ' + total + ' 件';
  glBindNewWindow(right);
  bar.appendChild(right);

  function mount() {
    if (!document.body) { return setTimeout(mount, 20); }
    if (document.getElementById('gl-nav')) return;
    document.body.insertBefore(bar, document.body.firstChild);
    document.body.appendChild(alertPanel);
    document.body.appendChild(alertToast);
    refreshAlerts();
    setInterval(refreshAlerts, 30000);
  }
  mount();
})();
