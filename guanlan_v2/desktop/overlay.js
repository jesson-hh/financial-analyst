// 掉线浮层。壳把本文件的内容 evaluate 进任意页面,再调 show/hide。
// 用浮层而不换页:DOM 保住了,恢复后撤掉浮层即可,不必重载丢失页面状态。
(function () {
  if (window.glShellOverlay) return;            // 幂等:每次 loaded 都会注入一遍
  var ID = 'gl-shell-overlay';

  function build(message) {
    var d = document.createElement('div');
    d.id = ID;
    d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:flex;' +
      'align-items:center;justify-content:center;background:rgba(28,24,20,.72);' +
      'font-family:"Noto Serif SC",serif;color:#f1ead9;text-align:center;';
    var inner = document.createElement('div');
    inner.style.cssText = 'max-width:520px;padding:26px 34px;background:#1c1814;' +
      'border:1px solid #a8392d;border-radius:3px;';
    var h = document.createElement('div');
    h.style.cssText = 'font-size:15px;letter-spacing:.16em;margin-bottom:10px;';
    h.textContent = '与 9999 的连接中断';
    var p = document.createElement('div');
    p.className = 'gl-ov-msg';
    p.style.cssText = 'font-family:Consolas,monospace;font-size:11px;color:#9e9482;';
    p.textContent = message || '';
    inner.appendChild(h); inner.appendChild(p); d.appendChild(inner);

    var acts = document.createElement('div');
    acts.style.cssText = 'margin-top:16px;display:flex;gap:10px;justify-content:center;';
    acts.appendChild(button('重试', function () { glCallApi('retry'); }));
    acts.appendChild(button('看日志', function () { glCallApi('open_log'); }));
    inner.appendChild(acts);
    return d;
  }

  // 同 boot.html:对 Python 侧的调用一律走 glCallApi('<字面量>'),
  // 否则 test_assets.py 的跨语言契约正则抽不到名字。
  function glCallApi(method) {
    var api = window.pywebview && window.pywebview.api;
    if (api && typeof api[method] === 'function') api[method]();
  }

  function button(label, onClick) {
    var b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = 'font-family:"Noto Serif SC",serif;font-size:13px;padding:6px 18px;' +
      'cursor:pointer;background:transparent;color:#f1ead9;border:1px solid #a8392d;border-radius:2px;';
    b.addEventListener('click', onClick);
    return b;
  }

  window.glShellOverlay = {
    show: function (message) {
      var cur = document.getElementById(ID);
      if (cur) { cur.querySelector('.gl-ov-msg').textContent = message || ''; return; }
      if (document.body) document.body.appendChild(build(message));
    },
    hide: function () {
      var cur = document.getElementById(ID);
      if (cur && cur.parentNode) cur.parentNode.removeChild(cur);
    }
  };
})();
