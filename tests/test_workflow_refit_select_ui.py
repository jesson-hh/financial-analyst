"""Show the actual refit flag without rewriting imported Agent graph params."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / "ui" / "factor" / "workflow.jsx"


def test_refit_select_accepts_json_booleans_and_legacy_chinese_values_without_mutation():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    source = WORKFLOW.read_text(encoding="utf-8")
    helper = source[source.index("function wfParamSelectValue("):source.index("function Node(")]
    script = helper + """
const graphs=[true,false,'是','否'].map(value=>Object.freeze({type:'source',params:Object.freeze({wf_refit:value})}));
const before=JSON.stringify(graphs);
const selected=graphs.map(g=>wfParamSelectValue(g.type,'wf_refit',g.params.wf_refit));
const unrelated=[wfParamSelectValue('source','scope',true),wfParamSelectValue('other','wf_refit',false),wfParamSelectValue('source','oos_frac',0.3)];
// Do not invent support for string flags which are not true in the executor.
const unsupported=wfParamSelectValue('source','wf_refit','true');
console.log(JSON.stringify({selected,unrelated,unsupported,unchanged:before===JSON.stringify(graphs)}));
"""
    result = subprocess.run([node, "-"], input=script, capture_output=True, text=True,
                            encoding="utf-8", check=True, timeout=20)
    data = json.loads(result.stdout)
    assert data["selected"] == ["是", "否", "是", "否"]
    assert data["unchanged"] is True
    assert data["unrelated"] == [True, False, 0.3]
    assert data["unsupported"] == "true"


def test_select_is_wired_for_display_only_and_retains_existing_edit_values():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "<select value={wfParamSelectValue(node.type, p.id, v)}" in source
    assert "onChange={e => onParam(p.id, e.target.value)}" in source
    assert "id: 'wf_refit', label: '滚动重训', type: 'select', value: '否', options: ['否', '是']" in source
    assert "wf_refit: p.wf_refit === '是' || p.wf_refit === true" in source
