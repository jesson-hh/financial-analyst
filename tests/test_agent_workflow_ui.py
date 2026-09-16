"""Agent graph handoff and server-owned run discovery; no live HTTP or execution."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
WF = (ROOT / "ui/factor/workflow.jsx").read_text(encoding="utf-8")
DATA = (ROOT / "ui/console/console-data.jsx").read_text(encoding="utf-8")


def node(script):
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node.js unavailable")
    result = subprocess.run([executable, "-"], input=script, capture_output=True,
                            text=True, encoding="utf-8", check=True)
    return json.loads(result.stdout)


def between(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def test_workflow_artifact_replay_only_adds_clickable_card_never_drives_bench():
    output = node("const window={GUANLAN_BACKEND:''};\n" + DATA + """
const payload={workspace_id:'rw_stock',session_id:'cs_123456789abc',workflow_id:'w123456789abc',name:'真实图',graph:{nodes:[{id:'f'}],edges:[]}};
const ev={type:'tool_result',id:1,ok:true,tool:'ww_workflow_generate',artifact:{kind:'workflow',page:'factor',channel:'guanlan.workflow.import',payload}};
const state=window.WW.apply(window.WW.initState(),ev);
const items=window.WW.deriveItems([ev],false);
const legacyBenchState=['activated','artifacts','benchClosed'].filter(key=>Object.prototype.hasOwnProperty.call(state,key));
console.log(JSON.stringify({legacyBenchState,card:items.find(x=>x.kind==='workflow'),bad:window.WW.normalizeWorkflowArtifact({...payload,workspace_id:'../../other'})}));
""")
    assert output["legacyBenchState"] == []
    assert output["card"]["workflowId"] == "w123456789abc"
    assert output["bad"] is None
    app = (ROOT / "ui/console/console-app.jsx").read_text(encoding="utf-8")
    assert "<WwBench" not in app


def test_import_message_requires_parent_origin_nonce_workspace_and_session():
    script = between(WF, "function wfAcceptGraphMessage(", "function wfNeedsReview(")
    output = node("const location={origin:'http://local'};const parent={};const window={parent};\n" + script + """
const payload={workspace_id:'rw_stock',session_id:'cs_test',graph:{nodes:[],edges:[]}};
const event={origin:location.origin,source:parent,data:{type:'guanlan.workflow.import',token:'wi_nonce',payload}};
const call=e=>!!wfAcceptGraphMessage(e,'wi_nonce','rw_stock','cs_test');
console.log(JSON.stringify([call(event),call({...event,source:{}}),call({...event,origin:'http://evil'}),call({...event,data:{...event.data,token:'wrong'}}),call({...event,data:{...event.data,payload:{...payload,workspace_id:'rw_other'}}}),call({...event,data:{...event.data,payload:{...payload,session_id:'cs_other'}}})]));
""")
    assert output == [True, False, False, False, False, False]
    assert "if (consumed) return" in WF
    assert "consumed = true" in WF


def test_new_manual_run_url_keeps_workspace_and_session_not_old_graph_pointer():
    helper = between(WF, "function wfPinRunLocation(", "function wfRunRecordError(")
    output = node("""
const location={href:'http://local/factor/page.html?ws=rw_stock&session_id=cs_123456789abc&run=wr_000000000000&import=wi_old&q=old#canvas'};
let path='';const window={history:{replaceState:(a,b,url)=>path=url}};
""" + helper + """
wfPinRunLocation('wr_123456789abc');const valid=path;
wfPinRunLocation('../../invalid');
console.log(JSON.stringify({valid,unchanged:path===valid}));
""")
    assert "ws=rw_stock" in output["valid"]
    assert "session_id=cs_123456789abc" in output["valid"]
    assert "run=wr_123456789abc" in output["valid"]
    assert "wi_old" not in output["valid"] and "q=old" not in output["valid"]
    assert output["unchanged"] is True


def test_foreign_or_missing_server_run_graph_is_fail_closed():
    helper = between(WF, "function wfRunRecordError(", "function wfAcceptGraphMessage(")
    output = node("const validateGraph=()=>'';" + helper + """
const rec={id:'wr_123456789abc',workspace_id:'rw_stock',graph:{nodes:[{id:'f'}],edges:[]}};
console.log(JSON.stringify([wfRunRecordError(rec,'rw_stock'),wfRunRecordError(rec,'rw_other'),wfRunRecordError({...rec,graph:null},'rw_stock')]));
""")
    assert output[0] == ""
    assert "不属于" in output[1]
    assert "没有可恢复" in output[2]


@pytest.mark.parametrize("status", ["running", "queued", "completed", "completed_with_errors", "failed", "interrupted"])
def test_history_restore_uses_server_graph_ids_and_results_without_any_run_post(status):
    helpers = between(WF, "function wfRunStatusLabel(", "function wfAcceptGraphMessage(")
    apply = between(WF, "  const applyDurableRun =", "  const pollDurableRun =")
    open_run = between(WF, "  const openHistoricalRun =", "  useEffect(() => {\n    loadServerRuns();")
    rec = {"ok": True, "id": "wr_123456789abc", "workspace_id": "rw_stock", "status": status,
           "name": "服务端历史图", "graph": {"nodes": [{"id": "server_node", "type": "formula"}], "edges": []},
           "node_states": {"server_node": {"state": "done"}}, "reason": "worker stopped"}
    if status in ("completed", "completed_with_errors"):
        rec["result"] = {"terminal": {"node_id": "server_node", "payload": {"_label": "真实结果", "ic": {"rank_ic_mean": 0.01}}}}
    output = node("""
const window={confirm:()=>true},location={search:'?ws=rw_stock'},WW_WORKSPACE_ID='rw_stock';
let wfName='当前未保存图',wfScope='workspace',running=false;
const mountedRef={current:true},historyRequestRef={current:0},runBusyRef={current:false},durableWaitIdRef={current:''},runPollRef={current:null};
const seen={requests:[],polls:[],hist:0};
const validateGraph=()=>'',wfGraphSnapshot=(nodes,edges)=>JSON.parse(JSON.stringify({nodes,edges})),_scopedPath=p=>p;
const wfSaveRunRef=r=>seen.ref=r,wfSaveLast=r=>seen.last=r,wfSaveReports=()=>{},wfReportKey=(n,s)=>s+':'+n;
const setRunState=v=>seen.states=v,setDurableWaiting=v=>seen.waiting=v,setResult=v=>seen.result=v,setRunning=v=>seen.running=v,setShowRes=v=>seen.shown=v,setRunErr=v=>seen.error=v;
const setResCache=f=>f({}),setHistoryOpening=()=>{},setNodes=v=>seen.nodes=v,setEdges=v=>seen.edges=v,setWfName=v=>wfName=v,setWfScope=v=>wfScope=v;
const graphNeedsPoolReview=()=>false,setAssetNeedsPool=()=>{},setSel=()=>{},setHistOpen=()=>{},pushHist=()=>seen.hist++,flash=(...x)=>seen.flash=x;
const pollDurableRun=(...x)=>seen.polls.push(x),_post=()=>{throw Error('forbidden run request')};
""" + f"const record={json.dumps(rec, ensure_ascii=False)};" + """
const _get=async p=>{seen.requests.push(p);return record};
""" + helpers + apply + open_run + """
(async()=>{await openHistoricalRun({id:record.id},true);console.log(JSON.stringify(seen))})();
""")
    assert output["nodes"][0]["id"] == "server_node"
    assert output["states"] == {"server_node": "done"}
    assert output["requests"] == ["/workflow/run/status/wr_123456789abc"]
    assert output["hist"] == 1
    assert len(output["polls"]) == (1 if status in ("queued", "running") else 0)
    if status.startswith("completed"):
        assert output["result"]["ic"]["rank_ic_mean"] == 0.01
    elif status in ("failed", "interrupted"):
        assert output["result"] is None and output["error"] == "worker stopped"
    else:
        assert output["result"] is None and output["waiting"] is True


def test_history_discovery_is_workspace_scoped_and_independent_of_local_storage():
    discover = between(WF, "  const loadServerRuns =", "  const openHistoricalRun =")
    assert "'/workflow/run/list?limit=30'" in discover
    assert "_scopedPath(" in discover and "localStorage" not in discover
    assert "rec.workspace_id" in discover
    assert "_post(" not in discover
    restore = between(WF, "  const openHistoricalRun =", "  useEffect(() => {\n    loadServerRuns();")
    assert "wfRunRecordError(rec, WW_WORKSPACE_ID)" in restore
    assert "cloneGraph(" not in restore


@pytest.mark.parametrize("response", [{"ok": False, "reason": "LLM unavailable"}, {"ok": True, "graph": {"nodes": []}}])
def test_ai_loop_failed_proposal_does_not_execute_template(response):
    loop = between(WF, "  const aiLoop =", "  const addNode =")
    output = node("""
const WW_READONLY=false,aiBusy=false;const seen={executed:0};
const setAiBusy=()=>{},setLoop=v=>{seen.loop=typeof v==='function'?v(seen.loop):v};
const validateGraph=()=> 'invalid graph';
const generateFromText=()=>{throw Error('template fallback forbidden')};
const cloneGraph=()=>{throw Error('invalid proposal cloned')};
const runGraph=()=>{seen.executed++;throw Error('execution forbidden')};
""" + f"const _post=async()=>({json.dumps(response)});" + loop + """
(async()=>{await aiLoop('精确条件');console.log(JSON.stringify(seen))})();
""")
    assert output["executed"] == 0
    assert "失败" in output["loop"]["step"] and "未执行" in output["loop"]["step"]
    assert "generateFromText" not in loop


def test_explicit_graph_open_retains_conversation_and_does_not_take_legacy_mailbox():
    app = (ROOT / "ui/console/console-app.jsx").read_text(encoding="utf-8")
    dock = (ROOT / "ui/console/console-workspace-dock.jsx").read_text(encoding="utf-8")
    assert "item.workspaceId !== activeWorkspaceId" in app
    assert "item.sessionId !== state.sid" in app
    assert "onOpenWorkflow={openWorkflow}" in app
    assert "'&session_id=' + encodeURIComponent(sessionId)" in dock
    assert "'&run=' + encodeURIComponent(run)" in dock
    assert "event.data.token === importToken" in dock
    assert "WW_QUERY.get('run') || WW_QUERY.get('import') || WW_QUERY.get('load')" in WF
    assert "?session_id=' + encodeURIComponent(WW_SESSION_ID)" in WF


def test_legacy_recovery_metadata_is_visible_and_never_silently_rewired():
    helpers = between(WF, "function wfNeedsReview(", "// 画布端口")
    output = node(helpers + """
const original={graph:{nodes:[{id:'old',type:'model'}],edges:[]},legacy_recovery:{classification:'historical_model_board',auto_repairable:false,blockers:[{reason:'原模型引用待核验'}],suggested_actions:['另建研究工作流']}};
const before=JSON.stringify(original);
console.log(JSON.stringify({review:wfNeedsReview(original),reason:wfReviewReason(original),unchanged:before===JSON.stringify(original)}));
""")
    assert output["review"] and output["unchanged"]
    assert "历史模型看板" in output["reason"] and "不可自动执行" in output["reason"]
    assert "另建研究工作流" in output["reason"]
    load = between(WF, "  const loadEntry =", "  const deleteEntry =")
    assert load.index("full.legacy_recovery.auto_repairable === false") < load.index("cloneGraph(")
    assert "历史引用（尚未核验）" in WF
