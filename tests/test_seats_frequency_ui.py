from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SEATS = ROOT / "ui" / "seats"


def _read(name: str) -> str:
    return (SEATS / name).read_text(encoding="utf-8")


def test_bars1_survives_symbol_rebuild_and_rejects_late_cross_code_response():
    app = _read("luozi-app.jsx")
    data = _read("luozi-data.jsx")

    assert "live.bars1 ? { bars1: live.bars1 } : {}" in app
    assert "seq !== bars1SeqRef.current" in app
    assert "responseCode !== requestCode" in app
    assert "bars1Meta" in app and "1min·陈旧" in app
    assert "return { code: _watchCode(j.code || code)" in data


def test_chart_timeframe_is_explicitly_display_only():
    app = _read("luozi-app.jsx")
    assert "图表周期·仅显示" in app
    assert "不改变 Agent 研判数据周期" in app


def test_watch_master_and_per_symbol_assignment_have_separate_ui_scopes():
    app = _read("luozi-app.jsx")
    fleet = _read("luozi-fleet.jsx")

    assert "function WatchControl" in app
    assert "<WatchControl watch={watch}" in app
    assert "WatchStrip" not in app
    assert "assignments={watchAssignments}" in app
    assert "onConfigureWatch={setWatchEdit}" in app
    assert "window.lzWatchSet(m.code" not in fleet
    assert "已纳入" in fleet and "旧版绑定" in fleet


def test_assignment_transport_is_closed_and_legacy_fallback_is_404_405_only():
    data = _read("luozi-data.jsx")
    app = _read("luozi-app.jsx")

    assert "fetch(API + '/seats/watch/assignments')" in data
    assert "method: 'PUT'" in data
    assert "res.status === 404 || res.status === 405" in data
    assert "supported: null, legacy: false" in data
    assert "if (assignmentMode.legacy)" in app
    assert "if (assignmentMode.supported !== true" in app
    assert "body.revision = currentAssignment.revision" in app
    assert "window.GL.archiveReady" in data
    assert "'/seats/watch/assignments/migrate'" in data
    assert "seq !== WATCH_ASSIGNMENT_FETCH_SEQ" in data


def test_archive_hydration_barrier_waits_for_all_pushes_before_assignment_migration():
    bus = (ROOT / "ui" / "_shared" / "guanlan-bus.js").read_text(encoding="utf-8")
    data = _read("luozi-data.jsx")
    assert "const archiveReady = new Promise" in bus
    assert "return Promise.all(pushes)" in bus
    assert "_resolveArchiveReady({ ok: ok" in bus
    fn = data[data.index("async function fetchWatchAssignments"):data.index("async function putWatchAssignment")]
    assert fn.index("await window.GL.archiveReady") < fn.index("/seats/watch/assignments/migrate") < fn.index("fetch(API + '/seats/watch/assignments')")


def test_archive_hydration_pushes_local_same_id_before_ready_and_keeps_server_only():
    bus = ROOT / "ui" / "_shared" / "guanlan-bus.js"
    script = r"""
const fs = require('fs');
const assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const initial = {
  artifacts: {
    strat_same: {id: 'strat_same', type: 'strategy', title: '本地策略', refs: ['local_binding'], ts: 20},
    research_local: {id: 'research_local', type: 'research', title: '本地研报', refs: [], ts: 21}
  },
  flags: {'seeded:v3': true},
  ts: 21
};
let stored = JSON.stringify(initial);
global.window = global;
window.addEventListener = function () {};
window.GUANLAN_BACKEND = 'http://test';
global.location = {protocol: 'http:', origin: 'http://test'};
global.localStorage = {
  getItem: function (key) { return key === 'guanlan:store:v1' ? stored : null; },
  setItem: function (key, value) { if (key === 'guanlan:store:v1') stored = value; },
  removeItem: function () {}
};
const pushed = [];
const releases = [];
global.fetch = function (url, options) {
  if (url.endsWith('/archive/list')) {
    return Promise.resolve({ok: true, json: function () { return Promise.resolve({ok: true, items: [
      {id: 'strat_same', type: 'strategy', title: '服务端旧策略', refs: ['server_binding'], ts: 10},
      {id: 'server_only', type: 'decision', title: '服务端独有', refs: [], ts: 11}
    ]}); }});
  }
  if (url.endsWith('/archive/put')) {
    pushed.push(JSON.parse(options.body).artifact);
    return new Promise(function (resolve) {
      releases.push(function () { resolve({ok: true, json: function () { return Promise.resolve({ok: true}); }}); });
    });
  }
  throw new Error('unexpected fetch ' + url);
};
eval(source);

(async function () {
  for (let i = 0; i < 50 && pushed.length < 2; i += 1) {
    await new Promise(function (resolve) { setTimeout(resolve, 0); });
  }
  assert.strictEqual(pushed.length, 2, 'all local non-demo sync items must be pushed');
  const same = pushed.find(function (a) { return a.id === 'strat_same'; });
  assert.deepStrictEqual(same.refs, ['local_binding']);
  assert.strictEqual(pushed.some(function (a) { return a.id === 'server_only'; }), false,
    'server-only merge must not be pushed back as a local item');
  assert.strictEqual(window.GL.get('server_only').title, '服务端独有');
  assert.strictEqual(window.GL.get('strat_same').title, '本地策略');

  let readySettled = false;
  window.GL.archiveReady.then(function () { readySettled = true; });
  await Promise.resolve();
  assert.strictEqual(readySettled, false, 'archiveReady must wait for every local push');
  releases.splice(0).forEach(function (release) { release(); });
  const ready = await window.GL.archiveReady;
  assert.strictEqual(ready.ok, true);
  assert.strictEqual(readySettled, true);
})().catch(function (error) {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        ["node", "-e", script, str(bus)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_new_dedicated_strategy_is_archived_before_assignment_put():
    data = _read("luozi-data.jsx")
    app = _read("luozi-app.jsx")

    assert "name: String((meta && meta.name) || c) + ' · 动量盯盘'" in data
    assert "fetch(API + '/archive/put'" in data
    ensure_at = app.index("await window.lzEnsureDedicatedWatchStrategy")
    put_at = app.index("await window.lzPutWatchAssignment", ensure_at)
    assert ensure_at < put_at


def test_judge_card_displays_and_submits_effective_evidence_without_silent_fallback():
    panels = _read("luozi-panels.jsx")
    app = _read("luozi-app.jsx")

    assert "assignment={currentAssignment}" in app
    assert "freq: effectiveTF || 'day'" in panels
    assert "未接通 · 未降级日线" in panels
    assert "依据 ·" in panels and "自动思考 ·" in panels
    assert "day + 'T'" in panels


def test_condition_monitor_uses_its_locked_indicator_timeframe():
    panels = _read("luozi-panels.jsx")
    app = _read("luozi-app.jsx")

    assert 'symbol={symbol}' in app
    assert "Promise.resolve(symbol && symbol.bars ? symbol.bars : null)" in panels
    assert "window.lzFetchBars5(code, 480)" in panels
    assert "条件单 · 触发监控" in panels
    assert "触发监控中" in panels


def test_strategy_editor_separates_judgment_defaults_from_risk_exit_and_guards_delete():
    foundry = _read("luozi-foundry.jsx")
    data = _read("luozi-data.jsx")

    assert "研判设置(策略默认;单票 assignment 可覆盖;不跟随图表周期)" in foundry
    assert "风险退出(止损止盈到价" in foundry
    assert "不是自动盯盘开关" in foundry
    assert "暂时无法核验单票盯盘引用，已取消删除" in foundry
    assert "!WATCH_ASSIGNMENTS_KNOWN" in data
    assert "WATCH_REFERENCED_STRATEGIES.has(s.id)" in data
    assert "await window.lzStrategyDelete(s.id)" in foundry
    assert "fetch(API + '/archive/remove'" in data
    assert "window.GL.remove(id)" in data


def test_both_live_minute_charts_use_the_durable_evidence_endpoint():
    data = _read("luozi-data.jsx")
    app = _read("luozi-app.jsx")
    assert "'/seats/bars_live?freq=1min" in data
    assert "'/seats/bars_live?freq=5min" in data
    assert "'/watch/bars?code='" not in data
    assert "byTime.set(b.date, b)" in app
    assert "bars5Meta: live5._meta || null" in app
    assert "5min·陈旧" in app


def test_manual_decide_preserves_stale_evidence_details_for_the_ui():
    data = _read("luozi-data.jsx")
    panels = _read("luozi-panels.jsx")
    assert "data_asof: j.data_asof || null" in data
    assert "detail: j.detail || null" in data
    assert "数据截至 ' + decide.data_asof" in panels


def test_all_changed_seats_assets_are_cache_busted_together():
    html = _read("观澜 · 落子.html")
    for name in ("luozi-data.jsx", "luozi-panels.jsx", "luozi-fleet.jsx", "luozi-foundry.jsx"):
        assert f'{name}?v=20260815w2' in html
    assert 'luozi-app.jsx?v=20260817n1' in html


def test_news_popover_has_readable_information_hierarchy():
    app = _read("luozi-app.jsx")
    html = _read("观澜 · 落子.html")
    assert 'className="lz-news-panel"' in app
    assert "eastmoney_kuaixun: '东方财富 · 快讯'" in app
    assert 'className="lz-news-item__time"' in app
    assert 'className="lz-news-item__headline"' in app
    assert 'aria-label="关闭快讯"' in app
    assert '.lz-news-panel { position:absolute' in html
    assert 'grid-template-columns:50px minmax(0,1fr)' in html
    assert 'width:min(430px,calc(100% - 24px))' in html
    assert '../_shared/guanlan-bus.js?v=6' in html
