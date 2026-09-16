# -*- coding: utf-8 -*-
"""研究空间内工作流/报告的前端命名空间与只读模板守护。"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "ui" / "factor" / "workflow.jsx"
HTML = ROOT / "ui" / "factor" / "观澜 · AI 工作流.html"


def _src() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _run_node(script: str) -> str:
    """Feed JS over stdin so Windows' command-line length limit cannot hide regressions."""
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is unavailable for the plain-JS behavior check")
    proc = subprocess.run(
        [node_bin, "-"], input=script, check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    return proc.stdout


def _graph_contract_js(src: str) -> str:
    builder_start = src.index("const SPECS = {")
    builder_end = src.index("// ───────── 克隆图", builder_start)
    contract_start = src.index("function dtCompatible(")
    contract_end = src.index("\nfunction stableJson", contract_start)
    return (
        "const HEADER=34,PAD=8,ROW=26,W=200;\n"
        + src[builder_start:builder_end]
        + "\n" + src[contract_start:contract_end]
        + "\nfunction _parseCodes(s){const p=Array.isArray(s)?s:String(s==null?'':s).split(/[\\s,;，、]+/);return p.map(x=>String(x).trim()).filter(Boolean);}\n"
    )


def test_workspace_local_keys_and_legacy_global_fallback():
    src = _src()
    assert "const WW_WORKSPACE_ID" in src
    assert "const WF_KEY = 'guanlan:wf:list:v1' + WW_NS" in src
    assert "const WF_REP_KEY = 'guanlan:wf:reports:v1' + WW_NS" in src
    assert "const WF_LAST_KEY = 'guanlan:wf:last:v1' + WW_NS" in src
    assert "if (!WW_WORKSPACE_ID) return path" in src


def test_workflow_and_report_requests_are_workspace_scoped():
    src = _src()
    assert "_post('/workflow/save', _withWorkspace(" in src
    assert "_get(_workflowPath('/workflow/list'))" in src
    assert "_get(_workflowPath('/workflow/get/'" in src
    assert "_post('/workflow/delete', _withWorkspace({ id }))" in src
    assert "include_global=true" in src
    assert "_post('/report/save', _withWorkspace(" in src
    assert "_get(_reportPath('/report/list'))" in src
    assert "_get(_reportPath('/report/get/'" in src
    assert "_post('/report/delete', _withWorkspace({ id }))" in src


def test_global_workflow_templates_are_read_only_and_save_as_space_copy():
    src = _src()
    assert "function wfIsGlobalTemplate" in src
    assert "全局只读模板" in src
    assert "修改后保存将生成当前空间副本" in src
    assert "!wfIsGlobalTemplate(w)" in src
    assert "workspace_id: WW_WORKSPACE_ID || null" in src


def test_reports_and_list_graphs_are_keyed_by_workflow_scope():
    src = _src()
    assert "function wfReportKey(name, scope)" in src
    assert "[wfReportKey(wfName, wfScope)]" in src
    assert "resCache[wfReportKey(full.name, scope)]" in src
    assert "scope: wfScope" in src
    assert "workflow_scope: wfScope" in src
    assert "const entryGraph = entry && entry.graph" in src


def test_backtest_run_is_durable_and_reconnects_after_board_switch():
    src = _src()
    assert "const WF_RUN_KEY = 'guanlan:wf:run:v1' + WW_NS" in src
    assert "function wfCanRunDurably(nodes)" in src
    assert "_post('/workflow/run/start', _withWorkspace(" in src
    assert "'/workflow/run/status/' + encodeURIComponent(runId)" in src
    assert "const ref = wfLoadRunRef()" in src
    # A persistent ?q/?asset/?load prefill must not hide an active or as-yet
    # unacknowledged durable run.  Prefill wins only after that run is settled.
    assert "pageQuery: location.search" in src
    assert "const sameLaunchQuery = typeof ref.pageQuery === 'string' && ref.pageQuery === location.search" in src
    assert "if (prefilledRef.current && !shouldWait && !sameLaunchQuery) return" in src
    reconnect = src[src.index("// 长回测重连优先使用"):src.index("  useEffect(() => {\n    const onKey", src.index("// 长回测重连优先使用"))]
    assert reconnect.index("const shouldWait =") < reconnect.index("prefilledRef.current && !shouldWait && !sameLaunchQuery")
    assert "setNodes(snap.nodes); setEdges(snap.edges)" in src
    assert "不能 cloneGraph 重发 id" in src
    assert "const runBusyRef = useRef(false)" in src
    assert "if (runBusyRef.current || running)" in src
    assert "setResult(null); setRunning(true); setShowRes(true); setRunErr('')" in src
    assert "启动响应中断 · 正在重连" in src
    assert "runBusyRef.current = false; setRunning(false); setShowRes(true); setRunErr(rec.reason || '运行记录不存在'); wfClearRunRef()" in src
    assert "const retryMs = missN < 20 ? 1500 : 5000" in src
    assert "只有服务端" in src and "明确 ok:false 或终态才能释放同一个 run_id" in src
    # 顶栏与画布浮动运行按钮共用同一 running 锁，避免双击产生第二个 run_id。
    assert "function Rail({ zoom, fit, onRun, running," in src
    assert "<Rail zoom={zoom} fit={fit} onRun={run} running={running}" in src
    assert "onClick={running ? undefined : onRun}" in src


def test_durable_wait_can_be_abandoned_locally_without_claiming_server_cancel():
    src = _src()
    assert "const [durableWaiting, setDurableWaiting] = useState(false)" in src
    assert "const durableWaitIdRef = useRef('')" in src
    assert "const stopWaitingForDurableRun = () =>" in src
    assert "durableWaitIdRef.current !== runId" in src
    assert "runPollRef.current = null" in src
    assert "wfClearRunRef()" in src
    assert "runBusyRef.current = false" in src
    assert "setDurableWaiting(false)" in src and "setRunning(false)" in src
    assert "不会取消服务端任务" in src
    assert "服务端任务可能仍在运行，重新运行可能重复计算" in src
    assert "停止等待 / 放弃重连" in src
    assert "onStopWaiting={stopWaitingForDurableRun}" in src
    assert "'/workflow/run/cancel'" not in src
    # 默认仍然无限重连：超过 20 次只降频，不自动解锁。
    assert "const retryMs = missN < 20 ? 1500 : 5000" in src
    assert "pollDurableRun(runId, ref, surfaceOnDone, missN + 1)" in src


def test_local_generator_keeps_source_out_of_chain_and_wires_context_through_tplg():
    src = _src()
    start = src.index("function generateFromText(q)")
    end = src.index("// ───────── 克隆图", start)
    block = src[start:end]
    assert "const srcParams =" in block
    assert "const steps = [{ type: 'formula', params: { expr } }, { type: 'feature'" in block
    assert "if (modelType) steps.push({ type: modelType, params: {} }, { type: 'mf'" in block
    assert "if (!modelType && !wantsBacktest) steps.push({ type: 'spearman'" in block
    assert "return tplG(srcParams, steps)" in block
    assert "const steps = [{ type: 'source'" not in block
    # tplG 的结构行为契约：单独创建 source，并把 data 端口接到 feature.src。
    tpl_start = src.index("function tplG(srcParams, steps)")
    tpl_end = src.index("// 空间私有因子", tpl_start)
    tpl = src[tpl_start:tpl_end]
    assert "const src = { id: nid(), type: 'source'" in tpl
    assert "c.nodes.find(n => n.type === 'feature')" in tpl
    assert "edges.push(connectCompatible(src, contextNode))" in tpl


def test_local_generator_runtime_graph_preserves_pool_context():
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is unavailable for the plain-JS graph-builder behavior check")
    src = _src()
    start = src.index("const SPECS = {")
    end = src.index("// ───────── 克隆图", start)
    dt_start = src.index("function dtCompatible(")
    dt_end = src.index("\n}\n\nconst WF_TERMINAL_DT", dt_start) + 2
    # 建图器本身是无 JSX 的纯 JS；只补齐它依赖的布局常量即可真执行。
    script = (
        "const HEADER=34,PAD=8,ROW=26,W=200;\n"
        + src[start:end]
        + "\n"
        + src[dt_start:dt_end]
        + "\nconst graph=generateFromText('动量回测 中证500');"
        + "process.stdout.write(JSON.stringify({graph,"
        + "sourceToSrc:dtCompatible('series','series','feature','source','src'),"
        + "sourceToFactor:dtCompatible('series','series','risk','source','factor')}));"
    )
    payload = json.loads(_run_node(script))
    graph = payload["graph"]
    source = next(node for node in graph["nodes"] if node["type"] == "source")
    feature = next(node for node in graph["nodes"] if node["type"] == "feature")
    formula = next(node for node in graph["nodes"] if node["type"] == "formula")
    assert source["params"]["universe"] == "csi500"
    assert {"from": [source["id"], "data"], "to": [feature["id"], "src"]} in graph["edges"]
    assert {"from": [formula["id"], "out"], "to": [feature["id"], "feat"]} in graph["edges"]
    assert payload["sourceToSrc"] is True
    assert payload["sourceToFactor"] is False


def test_local_generator_routes_multilingual_data_screen_and_pool():
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is unavailable for the plain-JS graph-builder behavior check")
    src = _src()
    start = src.index("const SPECS = {")
    end = src.index("// ───────── 克隆图", start)
    dt_start = src.index("function dtCompatible(")
    dt_end = src.index("\n}\n\nconst WF_TERMINAL_DT", dt_start) + 2
    queries = {
        "english_fast": "build a workflow using csi fast to screen MA 20 stocks",
        "english_all_a": "find MA5 candidates in all A",
        "chinese_fast": "用快速测试池选出沿10日均线上升的股票",
        "chinese_all_a": "用数据筛选 Skill 筛选全A股票",
        "explicit_snake": "data_screen for csi_fast",
        "explicit_words": "data screen skill for all A",
    }
    script = (
        "const HEADER=34,PAD=8,ROW=26,W=200;\n"
        + src[start:end]
        + "\n" + src[dt_start:dt_end]
        + "\nconst queries="
        + json.dumps(queries, ensure_ascii=False)
        + ";const out={};for(const [key,q] of Object.entries(queries)){"
        + "const graph=generateFromText(q);"
        + "const source=graph.nodes.find(n=>n.type==='source');"
        + "const screen=graph.nodes.find(n=>n.type==='data_screen');"
        + "out[key]={types:graph.nodes.map(n=>n.type),universe:source&&source.params.universe,"
        + "scope:source&&source.params.scope,ma_window:screen&&screen.params.ma_window,edges:graph.edges.length};}"
        + "process.stdout.write(JSON.stringify(out));"
    )
    out = json.loads(_run_node(script))
    for case in out.values():
        assert case["types"] == ["source", "data_screen"]
        assert case["edges"] == 1
    assert out["english_fast"] == {
        "types": ["source", "data_screen"], "universe": "csi_fast",
        "scope": "小池", "ma_window": 20, "edges": 1,
    }
    assert out["english_all_a"]["universe"] == "all_a"
    assert out["english_all_a"]["ma_window"] == 5
    assert out["chinese_fast"]["universe"] == "csi_fast"
    assert out["chinese_fast"]["ma_window"] == 10
    assert out["chinese_all_a"]["universe"] == "all_a"
    assert out["chinese_all_a"]["ma_window"] == 5
    assert out["explicit_snake"]["universe"] == "csi_fast"
    assert out["explicit_words"]["universe"] == "all_a"


def test_default_cards_and_local_generation_all_satisfy_the_graph_contract():
    src = _src()
    cases = {
        "analysis": "动量因子分析",
        "ic": "动量 IC 体检",
        "backtest": "动量回测",
        "ml_analysis": "动量 xgb 模型分析",
        "ml_ic": "动量 lightgbm IC",
        "ml_backtest": "动量随机森林回测",
        "screen": "筛出沿5日均线上升的股票",
        "orthogonalize": "价值因子正交化回测",
        "audit": "动量回测并做过拟合审计",
    }
    script = (
        _graph_contract_js(src)
        + "const graphs={seed:seedGraph()};"
        + "for(const [id,fn] of Object.entries(CARD_GRAPH))graphs['card_'+id]=fn();"
        + "const qs=" + json.dumps(cases, ensure_ascii=False) + ";"
        + "for(const [id,q] of Object.entries(qs))graphs[id]=generateFromText(q);"
        + "const out={};for(const [id,g] of Object.entries(graphs))out[id]={error:validateGraph(g.nodes,g.edges),types:g.nodes.map(n=>n.type),edges:g.edges};"
        + "process.stdout.write(JSON.stringify(out));"
    )
    out = json.loads(_run_node(script))
    assert all(not item["error"] for item in out.values()), out
    assert len([key for key in out if key.startswith("card_")]) == 8
    assert out["seed"]["types"] == ["source", "formula", "feature", "xgb", "mf", "analysis"]
    assert out["analysis"]["types"][-2:] == ["spearman", "analysis"]
    assert out["ic"]["types"][-2:] == ["spearman", "iccalc"]
    assert out["ml_analysis"]["types"][-3:] == ["xgb", "mf", "analysis"]


def test_graph_contract_rejects_cycles_ports_required_inputs_and_unreachable_terminals():
    src = _src()
    script = (
        _graph_contract_js(src)
        + "const mk=(id,type)=>({id,type,x:0,y:0,params:Object.assign({},...SPECS[type].params.map(p=>({[p.id]:p.value})))});"
        + "const formula=mk('f','formula'),feature=mk('fe','feature'),analysis=mk('a','analysis');"
        + "const cases={"
        + "bad_port:{nodes:[formula,analysis],edges:[{from:['f','missing'],to:['a','factor']}]},"
        + "missing_input:{nodes:[formula,analysis],edges:[]},"
        + "bad_type:{nodes:[formula,feature,analysis],edges:[{from:['f','out'],to:['fe','feat']},{from:['fe','fe'],to:['a','factor']}]},"
        + "cycle:{nodes:[formula,analysis],edges:[{from:['f','out'],to:['a','factor']},{from:['a','report'],to:['f','out']}]},"
        + "no_terminal:{nodes:[formula],edges:[]}};"
        + "const out={};for(const [id,g] of Object.entries(cases))out[id]=graphValidationErrors(g.nodes,g.edges);"
        + "process.stdout.write(JSON.stringify(out));"
    )
    out = json.loads(_run_node(script))
    assert any("输出端口不存在" in msg for msg in out["bad_port"])
    assert any("缺少必填输入" in msg for msg in out["missing_input"])
    assert any("连线类型不匹配" in msg for msg in out["bad_type"])
    assert any("环路" in msg for msg in out["cycle"])
    assert any("缺少终端节点" in msg for msg in out["no_terminal"])


def test_all_graph_ingress_paths_share_validation_and_durable_status_is_honest():
    src = _src()
    from guanlan_v2.workflow.api import _TERMINAL_TYPES
    main_types = src.split("const WF_DURABLE_MAIN_TYPES = {", 1)[1].split("};", 1)[0]
    assert all(f"{kind}: 1" in main_types for kind in _TERMINAL_TYPES)
    assert "nodes.some(n => n && WF_DURABLE_MAIN_TYPES[n.type])" in src
    for marker in [
        "保存前校验未通过", "载入失败 · 图未通过校验", "导入失败 · 图未通过校验",
        "深链生成未通过校验", "深链载入失败 · 图未通过校验", "运行前校验未通过",
    ]:
        assert marker in src
    assert "_run_status = 'completed_with_errors'" in src
    assert "_completed_with_errors = true" in src
    assert "status: completedWithErrors ? 'completed_with_errors' : status" in src
    assert "运行完成 · 含节点错误 · completed_with_errors" in src


def test_legacy_workflow_review_state_is_visible_in_history():
    src = _src()
    assert "function wfNeedsReview" in src
    assert "旧版只读 · 需重建" in src
    assert "wfReviewReason(w)" in src


def test_local_backtest_prefers_backend_canonical_factor_spec_and_digest():
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is unavailable for the local backtest behavior check")
    src = _src()
    start = src.index("  backtest: async (inputs, params, ctx) => {")
    end = src.index("  // 过拟合审计只消费", start)
    entry = src[start:end]
    script = (
        "const NODE_EXEC={\n" + entry + "\n};\n"
        + "(async()=>{"
        + "const raw={schema:'guanlan.factor_spec.v1',dependencies:[{name:'x',digest:'raw'}]};"
        + "const canonical={schema:'guanlan.factor_spec.v1',dependencies:[{name:'x',digest:'pinned-v2'}]};"
        + "const out=await NODE_EXEC.backtest({factor:{expr:'close',factor_spec:raw,factor_spec_digest:'raw-digest'}},{},{"
        + "universe:'csi500',post:async(path,payload)=>({ok:true,status:'ok',portfolio:{nav_series:[1]},"
        + "factor_spec:canonical,factor_spec_digest:'canonical-digest'})});"
        + "process.stdout.write(JSON.stringify(out.result));"
        + "})().catch(e=>{console.error(e);process.exit(1)});"
    )
    proc = subprocess.run(
        [node_bin, "-e", script], check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    result = json.loads(proc.stdout)
    assert result["factor_spec"]["dependencies"][0]["digest"] == "pinned-v2"
    assert result["factor_spec_digest"] == "canonical-digest"


def test_post_surfaces_non_2xx_backend_detail():
    src = _src()
    assert "if (!res.ok)" in src
    assert "b.error || b.detail || b.reason" in src
    assert "err.httpStatus = res.status" in src


def test_workspace_factor_asset_exact_loads_without_running_or_global_publish():
    src = _src()
    assert "const assetId = new URLSearchParams(location.search).get('asset')" in src
    assert "'/console/workspaces/' + encodeURIComponent(WW_WORKSPACE_ID) + '/assets/'" in src
    assert "function workspaceFactorGraph(asset)" in src
    assert "{ type: 'formula', params: { expr } }" in src
    assert "setWfScope('workspace')" in src
    assert "不会自动发布或进入落子" in src
    assert "成分/自定义池尚未结构化" in src
    assert "_workspace_pool_unresolved: true" in src
    assert "if (graphNeedsPoolReview(nodes))" in src
    assert "setAssetNeedsPool(graphNeedsPoolReview(g.nodes))" in src


def test_archived_stage_is_read_only():
    src = _src()
    assert "const WW_READONLY = WW_QUERY.get('readonly') === '1'" in src
    assert "if (WW_READONLY) { flash('归档空间只读'" in src
    assert "if (busy || WW_READONLY) return" in src
    assert "{!WW_READONLY && <span onClick={() => del(r.id)}" in src


def test_workflow_cache_bust_is_updated():
    html = HTML.read_text(encoding="utf-8")
    assert "workflow.jsx?v=20260905agent1" in html


def test_data_screen_is_a_screen_only_terminal_with_explicit_pool_guards():
    src = _src()
    assert "{ value: 'all_a', label: '全A · 筛选 Skill 专用' }" in src
    assert "const TERMINAL_DT = { data_screen: 1" in src
    assert "const WF_TERMINAL_DT = { data_screen: 1" in src
    assert "tsic: 1, event: 1, relstat: 1, risk: 1, garch: 1, attrib: 1, tvbeta: 1" in src
    assert "payload.__dt === 'data_screen'" in src
    assert "「数据筛选 Skill」必须显式连接「数据源」的 data → src" in src
    assert "「全A·筛选 Skill 专用」数据源只能连接「数据筛选 Skill」的 src 口" in src
    assert "_parseCodes(source.params && source.params.codes)" in src
    assert "universeCoverage.effective_valid_ratio != null" in src
    assert "universeCoverage.n_resolved == null ? universe.n_resolved : universeCoverage.n_resolved" in src
    assert "(+effectiveValid || 0) / resolvedN" in src
    assert "_data_screen_runs: secondaryScreens" in src
    assert "_parallel_terminal_refs: secondaryTerminals" in src
    assert "并行终端结果已独立保留" in src
    assert "并行数据筛选结果已独立保留" in src


def test_data_screen_generic_evidence_renderer_keeps_price_units():
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is unavailable for the data-screen formatter check")
    src = _src()
    start = src.index("function _screenSeriesUnit(")
    end = src.index("\n\nfunction ResultsDrawer", start)
    helpers = src[start:end]
    script = (
        helpers
        + "\nconst rule={type:'range',value:{type:'field',field:'close'}};"
        + "const out=_screenEvidenceValue(rule,{value:10,min:9,max:11});"
        + "process.stdout.write(out);"
    )
    proc = subprocess.run(
        [node_bin, "-e", script], check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    assert proc.stdout == "10.0000 ∈ [9.0000, 11.0000]"
    assert "%" not in proc.stdout


def test_data_screen_evidence_renderer_keeps_narrow_share_values_distinct():
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is unavailable for the data-screen formatter check")
    src = _src()
    start = src.index("function _screenSeriesUnit(")
    end = src.index("\n\nfunction ResultsDrawer", start)
    helpers = src[start:end]
    script = (
        helpers
        + "\nconst field={type:'field',field:'volume'};"
        + "const rangeRule={type:'range',value:field};"
        + "const compareRule={type:'compare',left:field,right:field,op:'<'};"
        + "const out=["
        + "_screenEvidenceValue(rangeRule,{value:12000,min:11999,max:12001}),"
        + "_screenEvidenceValue(compareRule,{left:11999,right:12001,op:'<'}),"
        + "_screenEvidenceValue(rangeRule,{value:1500000,min:1000000,max:2000000})];"
        + "process.stdout.write(JSON.stringify(out));"
    )
    proc = subprocess.run(
        [node_bin, "-e", script], check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    assert json.loads(proc.stdout) == [
        "12,000股 ∈ [11,999股, 12,001股]",
        "11,999股 < 12,001股",
        "150.000万股 ∈ [100.000万股, 200.000万股]",
    ]


def test_back_to_conversation_uses_console_shell_instead_of_archived_page():
    src = _src()
    assert "观澜 · 投研台.html" not in src
    assert "const WW_BACK_TO_CHAT_EVENT = 'guanlan:ww:back-to-conversation'" in src
    assert "window.parent.postMessage({ type: WW_BACK_TO_CHAT_EVENT }, location.origin)" in src
    assert "window.location.href = '../console/观澜 · 帷幄.html'" in src
    assert "onClick={wwBackToConversation}" in src
