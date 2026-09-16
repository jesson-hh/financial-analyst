"""Executable JS regressions for the September live-acceptance defects."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "ui/console/console-data.jsx"


def _js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for executable frontend regressions")
    harness = """const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const context = {window: {}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const WW = context.window.WW;
"""
    result = subprocess.run(
        [node, "-e", harness + script, str(DATA)], capture_output=True,
        text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_stream_fragments_preserve_only_model_whitespace_live_and_replay():
    _js("""const chunks = ['这是', '连续', '文本。', '\\n\\n', '1. ', '保留', '段落\\n', '2. 第二条'];
const start = [{id:1, type:'user_msg', text:'问', turn_id:'t1'}];
const events = start.concat(chunks.map((text,i) => ({id:i+2,type:'agent_delta',text})));
for (let end = 2; end <= events.length; end++) {
  const answer = WW.deriveItems(events.slice(0,end), true).find(x=>x.kind==='answer');
  assert.equal(answer.text, chunks.slice(0,end-1).join(''));
  assert.equal(answer.streaming, true);
}
const replay = WW.deriveItems(events.concat([{id:20,type:'task_update',status:'done',task_id:'t1'}]), false);
assert.equal(replay.find(x=>x.kind==='answer').text, chunks.join(''));
assert.ok(!replay.find(x=>x.kind==='answer').streaming);
""")


def test_stream_does_not_merge_across_tool_or_user_turn_boundaries():
    _js("""const events = [
 {id:1,type:'user_msg',turn_id:'t1',text:'first'},
 {id:2,type:'agent_delta',text:'查'}, {id:3,type:'agent_delta',text:'询中'},
 {id:4,type:'tool_call',tool:'ww_market_tape',args:{}},
 {id:5,type:'tool_result',tool:'ww_market_tape',ok:true,summary:'ok'},
 {id:6,type:'agent_delta',text:'完成'},
 {id:7,type:'user_msg',turn_id:'t2',text:'second'},
 {id:8,type:'agent_delta',text:'新回答'}];
const answers = WW.deriveItems(events,false).filter(x=>x.kind==='answer');
assert.equal(JSON.stringify(answers.map(x=>[x.text,x.turnId])), JSON.stringify([
 ['查询中','t1'],['完成','t1'],['新回答','t2']]));
""")


@pytest.mark.parametrize("status, count, state, label", [
    ("unavailable", 0, "unavailable", "名单不可用"),
    ("error", 0, "unavailable", "名单不可用"),
    ("partial", 3, "degraded", "数据降级"),
    ("partial_empty", 0, "degraded", "数据降级"),
    ("empty", 0, "empty", "查询无记录"),
    ("stale", 5, "degraded", "数据已过期"),
    ("ok", 0, "empty", "查询无记录"),
    ("ok", 2, "available", "上游返回正常"),
    (None, 0, "unknown", "数据可用性未核验"),
])
def test_snapshot_saved_is_not_source_available(status, count, state, label):
    asset = dict(kind="data", dataset_id="research.control_lists.live", status="saved",
                 upstream_status=status, record_count=count, summary="上游 ok", name="正常")
    _js(f"""const health = WW.dataAvailability({json.dumps(asset, ensure_ascii=False)});
assert.equal(health.state, {json.dumps(state)});
assert.equal(health.label, {json.dumps(label, ensure_ascii=False)});
assert.ok(health.note.length > 0);
""")


def test_only_data_assets_get_health_and_snapshot_status_is_visible():
    _js("""assert.equal(WW.dataAvailability({kind:'report',status:'saved'}), null);
assert.equal(WW.dataAvailability({kind:'data',dataset_id:'research.other',status:'saved'}).state, 'unknown');
""")
    dock = (ROOT / "ui/console/console-workspace-dock.jsx").read_text(encoding="utf-8")
    assert "data-source-availability={availability.state}" in dock
    assert "快照已保存（非数据可用结论）" in dock
    assert "data-report-status={view.status}" in dock
    assert "文件已归档不代表质量门通过" in dock
    html = (ROOT / "ui/console/观澜 · 帷幄.html").read_text(encoding="utf-8")
    for name in ("console-data.jsx", "console-workspace-dock.jsx"):
        assert f"{name}?v=20260909targeted1" in html


def test_individual_lists_surface_failed_unrequested_and_reference_only():
    _js("""const sources = WW.sourceAvailability({dataset_id:'research.control_lists.live',source_availability:[
 {id:'bis_entity',label:'BIS Entity List',status:'ok',reason_code:'OK'},
 {id:'ofac_sdn',label:'OFAC SDN',status:'error',reason_code:'HTTP_302'},
 {id:'cn_market_access',label:'准入',status:'reference_only',reason_code:'REFERENCE_ONLY'},
 {id:'bis_changes',label:'变更',status:'not_requested',reason_code:'NOT_REQUESTED'},
 {id:'other',label:'unknown',status:'saved',reason_code:'secret: https://not.allowed/'}]});
assert.equal(sources[0].labelStatus, '已取回');
assert.equal(sources[1].labelStatus, '不可用');
assert.equal(sources[2].labelStatus, '仅原文入口，未结构化');
assert.equal(sources[3].labelStatus, '本次未查询');
assert.equal(sources[4].labelStatus, '未核验');
assert.equal(sources[4].reason, '');
assert.equal(WW.sourceAvailability({dataset_id:'research.control_lists.live'}).length, 0);
assert.equal(WW.sourceAvailability({dataset_id:'other',source_availability:[{}]}).length, 0);
""")
