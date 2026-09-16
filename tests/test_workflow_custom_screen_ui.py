"""Imported custom rules must not masquerade as editable MA5 defaults."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "ui/factor/workflow.jsx"


def _js(script):
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node.js unavailable")
    source = WORKFLOW.read_text(encoding="utf-8")
    helper = source[source.index("function wfCustomScreenSummary("):source.index("function CustomScreenSummary(")]
    result = subprocess.run([executable, "-"], input=helper + script,
                            capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    return json.loads(result.stdout)


def test_custom_rules_show_real_nested_filters_and_do_not_mutate_graph():
    result = _js("""
const node={id:'s',type:'data_screen',params:{
  selector:{conditions:[{id:'ma20',label:'MA20 严格上升',type:'strict_rise'},
    {id:'pullback',label:'距离近10日高点回落至少1%',type:'range'}],combine:'all'},
  filters:{min_avg_amount_yuan:50000000,amount_window:20,amount_min_observations:18,
    exclude_st:false,require_asof_trade:true,require_strict_metadata_pit:true},
  as_of:'2026-09-11',top_n:10}};
const before=JSON.stringify(node);
const summary=wfCustomScreenSummary(node,[],[]);
console.log(JSON.stringify({summary,unchanged:before===JSON.stringify(node)}));
""")
    summary = result["summary"]
    assert result["unchanged"] is True
    assert summary["valid"] is True
    assert [r["label"] for r in summary["rules"]] == ["MA20 严格上升", "距离近10日高点回落至少1%"]
    assert summary["liquidity"] == "20 日均额 ≥ 0.5 亿元"
    assert summary["observations"] == "18/20 日有效成交额"
    assert summary["excludeST"] == "否" and summary["requireTrade"] == "是"
    assert summary["strictPIT"] == "是"
    assert summary["asOf"] == "2026-09-11" and summary["topN"] == 10


def test_legacy_nodes_without_custom_conditions_keep_original_controls():
    result = _js("""
const nodes=[{type:'data_screen',params:{preset:'ma_trend',ma_window:5}},
 {type:'data_screen',params:{}},{type:'source',params:{selector:{conditions:[]}}}];
console.log(JSON.stringify(nodes.map(n=>wfCustomScreenSummary(n,[],[]))));
""")
    assert result == [None, None, None]


def test_empty_or_invalid_custom_conditions_do_not_reveal_fake_legacy_defaults():
    result = _js("""
console.log(JSON.stringify([[],null,'bad'].map(conditions=>wfCustomScreenSummary(
 {type:'data_screen',params:{selector:{conditions}}},[],[]))));
""")
    assert all(r is not None and r["valid"] is False for r in result)
    assert all(r["rules"] == [] for r in result)


def test_direct_conditions_compatibility_and_explicit_zero_false_are_preserved():
    result = _js("""
const node={type:'data_screen',params:{conditions:[{id:'custom',type:'range'}],
  min_avg_amount_yi:0,amount_window:7,exclude_st:false,require_asof_trade:false,topN:3}};
console.log(JSON.stringify(wfCustomScreenSummary(node,[],[])));
""")
    assert result["liquidity"] == "7 日均额 ≥ 0 亿元"
    assert result["observations"] == "7/7 日有效成交额"
    assert result["excludeST"] == result["requireTrade"] == "否"
    assert result["topN"] == 3 and result["rules"][0]["label"] == "custom"


def test_asof_uses_only_explicit_date_or_the_actual_connected_source():
    result = _js("""
const screen={id:'screen',type:'data_screen',params:{selector:{conditions:[{id:'x'}]}}};
const sources=[{id:'unrelated',type:'source',params:{end:'1999-01-01'}},
 {id:'real',type:'source',params:{end:'2026-09-11'}}];
const edges=[{from:['real','data'],to:['screen','src']}];
const connected=wfCustomScreenSummary(screen,sources,edges);
const multiple=wfCustomScreenSummary(screen,sources,[...edges,{from:['unrelated','data'],to:['screen','src']}]);
const absent=wfCustomScreenSummary(screen,sources,[]);
const override=wfCustomScreenSummary({...screen,params:{...screen.params,as_of:'2026-08-31'}},sources,edges);
console.log(JSON.stringify({connected,multiple,absent,override}));
""")
    assert result["connected"]["asOf"] == "2026-09-11"
    assert result["connected"]["asOfSource"] == "来自所连数据源"
    assert result["override"]["asOf"] == "2026-08-31"
    assert "数据源不明确" in result["multiple"]["asOf"]
    assert "运行时确定" in result["absent"]["asOf"]


def test_backend_defaults_are_only_used_for_absent_fields_and_bad_and_is_flagged():
    result = _js("""
const make=params=>wfCustomScreenSummary({type:'data_screen',params},[],[]);
const defaults=make({selector:{conditions:[{id:'x'}]},filters:{}});
const bad=make({selector:{conditions:[{id:'x'}],combine:'any'},filters:{exclude_st:'false',min_avg_amount_yuan:null}});
console.log(JSON.stringify({defaults,bad}));
""")
    assert result["defaults"]["liquidity"] == "20 日均额 ≥ 1 亿元"
    assert result["defaults"]["excludeST"] == "是"
    assert result["bad"]["valid"] is False
    assert result["bad"]["excludeST"] == "无效配置"
    assert result["bad"]["liquidity"] == "20 日均额 ≥ 无效配置"


def test_node_hides_all_legacy_parameter_rows_and_custom_summary_is_read_only_bounded():
    source = WORKFLOW.read_text(encoding="utf-8")
    node = source[source.index("function Node("):source.index("function Rail(")]
    component = source[source.index("function CustomScreenSummary("):source.index("function wfParamSelectValue(")]
    assert "const customScreen = wfCustomScreenSummary(node, nodes, edges);" in node
    assert "rowsOf(spec).filter(r => !customScreen || r.kind !== 'param')" in node
    assert "{customScreen ? <CustomScreenSummary summary={customScreen} /> : null}" in node
    assert "height: SPECS.data_screen.params.length * ROW" in component
    assert "minHeight: 0, overflowY: 'auto'" in component
    assert "onWheel={e => e.stopPropagation()}" in component
    assert "全部满足（AND）" in component and "帷幄／完整图配置" in component
    assert "<input" not in component and "<select" not in component and "onParam" not in component
    assert "dangerouslySetInnerHTML" not in component
