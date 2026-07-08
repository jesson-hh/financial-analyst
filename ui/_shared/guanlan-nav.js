// 观澜 · 全局模块导航条 guanlan-nav.js
// 顶部统一一栏切换五个模块,高亮当前页。flex 列布局把内容下压,兼容内部 height:100vh。
(function () {
  // 帷幄嵌入卫生:?embed=1 时本页被装进帷幄右栏 iframe → 不注入导航条与 body/#root 强样式
  if (new URLSearchParams(location.search).get('embed') === '1') return;
  var MODULES = [
    { label: '帷幄', file: '../console/观澜 · 帷幄.html', home: true },
    { label: '席位 · 落子', file: '../seats/观澜 · 落子.html' },
    { label: '选股', file: '../screen/观澜 · 选股.html' },
    { label: 'AI投研', file: '../industry/观澜 · AI投研.html' },
    { label: '全球情绪', file: '../macro/观澜 · 全球情绪.html' },
    { label: '资金流向', file: '../fundflow/观澜 · 资金流向.html' },
  ];
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
    '#gl-right{margin-left:auto;align-self:center;display:flex;align-items:center;gap:7px;' +
    'font-family:var(--mono),monospace;font-size:10px;color:var(--ink-3,#9e9482);letter-spacing:.04em;cursor:pointer;}' +
    '#gl-right .dot{width:6px;height:6px;border-radius:50%;background:var(--dai,#4a6b5c);}' +
    '#root{flex:1 1 auto!important;height:auto!important;min-height:0!important;}' +
    '#root>*{height:100%!important;}';
  document.head.appendChild(st);

  var bar = document.createElement('div');
  bar.id = 'gl-nav';
  bar.innerHTML =
    '<a id="gl-brand" href="../graph/观澜 · 研究图谱.html" style="text-decoration:none"><span class="s">觀</span><span class="w">觀瀾</span></a>' +
    '<span class="sep"></span>';

  MODULES.forEach(function (m) {
    var on = here === m.file.split('/').pop() || (here === '' && m.home);
    var a = document.createElement('a');
    a.className = 'gl-tab' + (on ? ' on' : '');
    a.href = m.file;
    a.textContent = m.label;
    bar.appendChild(a);
  });

  // 右:共享档案库物料数(读 GL,带轻动效)
  var right = document.createElement('a');
  right.id = 'gl-right';
  right.href = '../graph/观澜 · 研究图谱.html';
  right.style.textDecoration = 'none';
  var total = 0;
  try { total = (window.GL && GL.stats && GL.stats().total) || 0; } catch (e) {}
  right.innerHTML = '<span class="dot"></span>共享档案库 · ' + total + ' 件';
  bar.appendChild(right);

  function mount() {
    if (!document.body) { return setTimeout(mount, 20); }
    if (document.getElementById('gl-nav')) return;
    document.body.insertBefore(bar, document.body.firstChild);
  }
  mount();
})();
