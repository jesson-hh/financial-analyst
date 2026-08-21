# -*- coding: utf-8 -*-
"""研究空间内工作流/报告的前端命名空间与只读模板守护。"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "ui" / "factor" / "workflow.jsx"
HTML = ROOT / "ui" / "factor" / "观澜 · AI 工作流.html"


def _src() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


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
    assert "workflow.jsx?v=20260813w3" in html
