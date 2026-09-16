"""Analysis curves must not certify a failed or absent backtest terminal."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / "ui" / "factor" / "workflow.jsx"


def _run(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    source = WORKFLOW.read_text(encoding="utf-8")
    helpers = source[source.index("const _KPI_TIP ="):source.index("function _screenSeriesUnit(")]
    result = subprocess.run([node, "-"], input=helpers + script, capture_output=True,
                            text=True, encoding="utf-8", check=True, timeout=20)
    return json.loads(result.stdout)


@pytest.mark.parametrize("record", [
    {"ok": True, "__dt": "report", "method": "lgbm", "_completed_with_errors": True},
    {"ok": True, "__dt": "report", "_compose": True},
    {"ok": False, "method": "backtest_vector"},
    {"portfolio": {"net_ann": 0.2}},
])
def test_non_backtest_terminal_never_claims_net_returns_or_successful_backtest(record):
    data = _run("const result=" + json.dumps(record) + ";" + """
const before=JSON.stringify(result), p=wfResultPresentation(result);
console.log(JSON.stringify({p,tip:_kpiTip(p.annualLabel,p.isBacktest),unchanged:before===JSON.stringify(result)}));
""")
    assert data["p"]["isBacktest"] is False
    assert data["p"]["metricTitle"] == "分析指标 · 非净回测"
    assert data["p"]["annualLabel"] == "年化(指示)"
    assert "未扣成本" in data["p"]["diagnostic"]
    assert "/factor/report" not in data["p"]["diagnostic"]
    assert "不是实际净回测收益" in data["tip"]
    assert "不证明下游回测节点执行成功" in data["p"]["note"]
    assert data["unchanged"] is True


def test_actual_backtest_preserves_net_and_gross_distinction_and_actual_endpoint():
    data = _run("""
const p=wfResultPresentation({ok:true,method:'backtest_vector',portfolio:{net_ann:.12,gross_ann:.15}});
console.log(JSON.stringify({p,net:_kpiTip(p.annualLabel,p.isBacktest),gross:_kpiTip('年化(毛)',p.isBacktest)}));
""")
    assert data["p"]["isBacktest"] is True
    assert data["p"]["annualLabel"] == "年化(净)"
    assert data["p"]["metricTitle"] == "回测指标"
    assert data["p"]["diagnostic"] == "引擎回测 · /backtest/vector"
    assert "扣除已配置交易成本" in data["net"]
    assert "未扣交易成本" in data["gross"]
    assert "净指标计入已配置的交易成本" in data["p"]["note"]


def test_insight_uses_selected_terminal_even_when_backtest_failed():
    source = WORKFLOW.read_text(encoding="utf-8")
    start = source.index("  const insight = result")
    insight = source[start:source.index("  return (", start)]
    data = _run("""
const result={ok:true,method:'lgbm',_warnings:['backtest 节点失败：缺退出行情']};
const presentation=wfResultPresentation(result), isPf=false, spread=null, nav=[1,1.1];
const _pct=x=>String(x);
""" + insight + "console.log(JSON.stringify({insight}));")
    assert "backtest 节点失败" in data["insight"]
    assert "因子分析" in data["insight"]
    assert "2 个分析组合曲线点" in data["insight"]
    assert "真引擎回测" not in data["insight"]


def test_drawer_wiring_changes_labels_without_changing_metric_data_selection():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "<RCard title={presentation.metricTitle}>" in source
    assert "title={_kpiTip(l, presentation.isBacktest)}" in source
    assert "{presentation.note}</div>" in source
    assert "completedWithErrors ? '运行完成 · 含节点错误' : presentation.resultTitle" in source
    # The fix is presentational: do not drop, rewrite, or recompute the existing value.
    assert "[presentation.annualLabel, _pct(pf.net_ann != null ? pf.net_ann : pf.ann_return), ((pf.net_ann != null ? pf.net_ann : pf.ann_return) >= 0 ? 'up' : 'down')]" in source
