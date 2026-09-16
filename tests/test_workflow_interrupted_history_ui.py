"""Display restart interruption without changing the durable execution state."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / "ui" / "factor" / "workflow.jsx"


def test_legacy_failed_orphan_is_displayed_as_interrupted_without_mutating_record():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    source = WORKFLOW.read_text(encoding="utf-8")
    helper = source[source.index("function wfRunStatusLabel("):source.index("function wfPinRunLocation(")]
    script = helper + """
const records=[
  {status:'failed',interrupted:true},
  {status:'failed',failure_kind:'orphaned'},
  {status:'failed',failure_kind:'interrupted'},
  {status:'interrupted'},
  {status:'failed',reason:'ordinary node error'},
  {status:'running',interrupted:true},
  {status:'completed',failure_kind:'orphaned'}
];
const before=JSON.stringify(records);
const labels=records.map(rec=>wfRunStatusLabel(rec.status,rec));
console.log(JSON.stringify({labels,unchanged:before===JSON.stringify(records),legacy:wfRunStatusLabel('failed')}));
"""
    result = subprocess.run([node, "-"], input=script, capture_output=True, text=True,
                            encoding="utf-8", check=True, timeout=20)
    data = json.loads(result.stdout)
    assert data["labels"] == ["已中断", "已中断", "已中断", "已中断", "失败", "运行中", "已完成"]
    assert data["unchanged"] is True
    assert data["legacy"] == "失败"


def test_history_explanation_uses_interruption_evidence_not_only_status_spelling():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "wfRunStatusLabel(rec.status, rec)" in source
    assert "{wfRunIsInterrupted(rec) && <div" in source
    assert "不支持断点续算，不会自动重跑。" in source
    # The display fix must not convert an orphan to a live/replayable task.
    assert "const active = status === 'queued' || status === 'running'" in source
    assert "rec.status === 'running' || rec.status === 'queued' ? '继续查看运行' : '恢复原图 / 查看结果'" in source
