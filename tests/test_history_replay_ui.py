"""New historical-selector UI: typed isolation, bounded JSON and honest ledger rendering."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("GUANLAN_HISTORY_UI_SOURCE", ROOT / "ui/factor/workflow.jsx")).read_text(encoding="utf-8")


def between(start, end):
    return SOURCE[SOURCE.index(start):SOURCE.index(end, SOURCE.index(start))]


def test_uncertified_old_history_drawer_hides_performance_and_export():
    blocked = between("function DailyScreenResults(", "const view = wfDailyScreenView(result)")
    assert "result.price_quality.performance_ready !== true" in blocked
    assert 'data-daily-screen-blocked="true"' in blocked
    assert "不能用于策略排名" in blocked
    assert "onClick={onExport}" not in blocked
    assert "nav.map" not in blocked and "view.metrics" not in blocked


def js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    result = subprocess.run([node, "-"], input=script, capture_output=True,
                            text=True, encoding="utf-8", check=True, timeout=30)
    return json.loads(result.stdout)


PARSER = between("function wfParseHistoryBranches(", "function HistoryBranchesEditor(")
SPECS = between("const SPECS =", "const CATALOG =")
CONTRACT = between("function dtCompatible(", "function stableJson(")
BRANCH = {"selector": {"combine": "all", "conditions": [
    {"id": "up", "label": "MA20 连续3日上升", "type": "strict_rise",
     "series": {"type": "rolling_mean", "field": "close", "window": 20}, "transitions": 3}
]}, "filters": {"min_avg_amount_yuan": 50000000}}


def test_parser_is_bounded_json_only_and_accepts_single_and_or_branch_shapes():
    # CI must not depend on the user's ignored experiment artifacts or live data.
    branches = [[BRANCH], [BRANCH], [BRANCH], [BRANCH, BRANCH], [BRANCH, BRANCH], [BRANCH, BRANCH]]
    result = js(PARSER + "const values=" + json.dumps(branches, ensure_ascii=False) + """;

const before=JSON.stringify(values);
const parsed=values.map(v=>wfParseHistoryBranches(JSON.stringify(v)));
console.log(JSON.stringify({counts:parsed.map(x=>x.length),unchanged:before===JSON.stringify(values)}));
""")
    assert result == {"counts": [1, 1, 1, 2, 2, 2], "unchanged": True}
    assert "JSON.parse(text)" in PARSER
    assert "eval(" not in PARSER and "new Function" not in PARSER


@pytest.mark.parametrize("value", [
    None, [], [{}], [{"selector": {"combine": "any", "conditions": BRANCH["selector"]["conditions"]}}],
    [{"selector": {"conditions": [{"type": "python"}]}}],
    [{"selector": {"conditions": BRANCH["selector"]["conditions"] * 17}}],
    [BRANCH] * 9,
    [{"selector": {"preset": "ma_trend", "conditions": BRANCH["selector"]["conditions"]}}],
])
def test_parser_rejects_invalid_or_unbounded_branches(value):
    result = js(PARSER + "const text=" + json.dumps(json.dumps(value)) + """;

let error='';try{wfParseHistoryBranches(text)}catch(e){error=e.message}
console.log(JSON.stringify({error}));
""")
    assert result["error"]


def test_parser_accepts_explicit_preset_and_does_not_invent_defaults():
    value = [{"selector": {"preset": "ma_trend", "params": {"ma_window": 20}}}]
    result = js(PARSER + "const value=" + json.dumps(value) + ";console.log(JSON.stringify(wfParseHistoryBranches(JSON.stringify(value))));")
    assert result == value
    editor = between("function HistoryBranchesEditor(", "function wfCustomScreenSummary(")
    assert "原样JSON，未补默认条件" in editor
    assert "JSON.stringify(branches[i], null, 2)" in editor


def test_parser_rejects_javascript_and_oversized_text():
    result = js(PARSER + """

const inputs=['(()=>{throw Error("executed")})()', ' '.repeat(131073)];
console.log(JSON.stringify(inputs.map(text=>{try{wfParseHistoryBranches(text);return ''}catch(e){return e.message}})));
""")
    assert "语法无效" in result[0] and "128 KiB" in result[1]


def test_snapshot_candidates_cannot_enter_history_or_daily_backtest():
    result = js(between("function dtCompatible(", "const WF_TERMINAL_DT") + """

console.log(JSON.stringify([
 dtCompatible('series','series','history_screen','source','src'),
 dtCompatible('screen_history','screen_history','daily_backtest','history_screen','history'),
 dtCompatible('data_screen','screen_history','daily_backtest','data_screen','history'),
 dtCompatible('data_screen','series','history_screen','data_screen','src'),
 dtCompatible('screen_history','factor','backtest','history_screen','factor'),
 dtCompatible('screen_history','screen_history','daily_backtest','formula','history'),
 dtCompatible('series','factor','backtest','formula','factor')
]));
""")
    assert result == [True, True, False, False, False, False, True]


def graph_context():
    return SPECS + PARSER + CONTRACT + "const _parseCodes=x=>Array.isArray(x)?x:String(x||'').split(/[, ]+/).filter(Boolean);"


def test_three_node_graph_is_valid_and_required_ports_fail_closed():
    branch = json.dumps(BRANCH, ensure_ascii=False)
    result = js(graph_context() + """
const nodes=[
 {id:'src',type:'source',params:{universe:'csi800',research_freq:'day'}},
 {id:'hist',type:'history_screen',params:{branches:[BRANCH]}},
 {id:'bt',type:'daily_backtest',params:{topn:10,rebalance:'day',weight_mode:'fixed_slots'}}
];
const edges=[{from:['src','data'],to:['hist','src']},{from:['hist','history'],to:['bt','history']}];
const valid=graphValidationErrors(nodes,edges);
const missing=graphValidationErrors(nodes,[]);
const weekly=graphValidationErrors(nodes.map(n=>n.id==='src'?{...n,params:{...n.params,research_freq:'week'}}:n),edges);
console.log(JSON.stringify({valid,missing,weekly}));
""".replace("BRANCH", branch))
    assert result["valid"] == []
    assert any("src" in x for x in result["missing"])
    assert any("history" in x for x in result["missing"])
    assert any("周/月频" in x for x in result["weekly"])


def test_new_graph_uses_durable_server_runs_without_changing_legacy_support():
    helper = between("const WF_DURABLE_TYPES =", "function wfAgo(")
    result = js(helper + """

console.log(JSON.stringify([
 wfCanRunDurably([{type:'source'},{type:'history_screen'},{type:'daily_backtest'}]),
 wfCanRunDurably([{type:'source'},{type:'data_screen'}]),
 wfCanRunDurably([{type:'source'},{type:'formula'},{type:'backtest'}])
]));
""")
    assert result == [True, True, True]


def test_browser_adapter_scopes_requests_and_refuses_snapshot_input():
    executors = between("const NODE_EXEC =", "  // —— IO 层: source") + "};"
    result = js("const WW_WORKSPACE_ID='rw_general';" + PARSER + executors + """
(async()=>{
 const calls=[],ctx={universe:'csi800',start:'2023-01-01',end:'2026-09-11',codes:[],
 post:async(path,body)=>{calls.push({path,body});return {ok:true,method:path.includes('daily-screen')?'daily_screen_backtest':'history_screen',label:'test'}}};
 const history=await NODE_EXEC.history_screen({src:{__dt:'series'}},{branches:[BRANCH]},ctx);
 const done=await NODE_EXEC.daily_backtest(history,{topn:10},ctx);
 let rejected='';try{await NODE_EXEC.daily_backtest({history:{__dt:'data_screen',candidates:[]}},{},ctx)}catch(e){rejected=e.message}
 console.log(JSON.stringify({calls,history,done,rejected}));
})();
""".replace("BRANCH", json.dumps(BRANCH, ensure_ascii=False)))
    assert [x["path"] for x in result["calls"]] == ["/workflow/history-screen", "/backtest/daily-screen"]
    assert all(x["body"]["workspace_id"] == "rw_general" for x in result["calls"])
    assert result["calls"][0]["body"]["context"] == {
        "universe": "csi800", "start": "2023-01-01", "end": "2026-09-11", "codes": [], "research_freq": "day"
    }
    assert result["history"]["history"]["__dt"] == "screen_history"
    assert result["done"]["result"]["method"] == "daily_screen_backtest"
    assert "单日候选快照" in result["rejected"]


def test_daily_view_preserves_zero_missing_and_stale_warning_evidence():
    result = js(between("function wfDailyScreenView(", "function DailyScreenResults(") + """

const r={summary:{total_return:0,ann_return:-.1,average_cash_weight:1,trade_count:0,costs_paid:0},
 portfolio:{nav_series:[['2025-01-01',1],['bad',null],['2025-01-02',0.9]]},
 diagnostics:{stale_position_days:3,final_unresolved_positions:[{instrument:'SH600001'}]},
 warnings:['warning'],assumptions:['research'],daily:Array.from({length:25},(_,i)=>({date:String(i)})),
 trades:Array.from({length:40},(_,i)=>({date:String(i)})),candidates:[]};
console.log(JSON.stringify(wfDailyScreenView(r)));
""")
    assert result["metrics"][0][1] == 0
    assert result["metrics"][2][1] is None
    assert result["metrics"][6][1] == 0 and result["metrics"][7][1] == 0
    assert len(result["nav"]) == 2
    assert result["staleDays"] == 3 and len(result["unresolved"]) == 1
    assert len(result["daily"]) == 20 and len(result["trades"]) == 30


def test_ui_has_actual_rules_explicit_apply_readonly_guard_and_distinct_drawer():
    editor = between("function HistoryBranchesEditor(", "function wfCustomScreenSummary(")
    assert "onChange(parsed); setEditing(false)" in editor
    assert "maxLength={131072}" in editor
    assert "disabled={WW_READONLY}" in editor
    assert "未补默认条件" in editor
    assert "dangerouslySetInnerHTML" not in editor
    results = between("function DailyScreenResults(", "function ResultsDrawer(")
    for text in ["研究模拟，不会下单", "固定槽位未用部分保留现金", "不按未来收益是否有值换票",
                 "存在陈旧估值", "不是强制清仓收益", "不是选股命中率", "完整模拟假设与数据限制"]:
        assert text in results
    assert "result.method === 'daily_screen_backtest'" in SOURCE
    assert "daily_screen_backtest: '日频模拟调仓'" in SOURCE
